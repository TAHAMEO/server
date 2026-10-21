package serverauth;

import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import net.minecraft.ChatFormatting;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.core.BlockPos;
import net.minecraft.core.NonNullList;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.Tag;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.TickTask;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.effect.MobEffects;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraftforge.fml.loading.FMLPaths;
import serverauth.AccountStore.Account;

/**
 * Tracks players who have not logged in yet and does the password work.
 * Game state is only changed on the server thread; hashing runs on a worker thread
 * so a slow CPU never freezes the game.
 */
final class AuthManager {
    private static final String INVENTORY_KEY = "serverauth_inventory";
    private static final String LOCATION_KEY = "serverauth_location";
    private static final String BLIND_KEY = "serverauth_blinded";
    private static final long REMINDER_MILLIS = 10_000;
    private static final int MAX_PASSWORD_LENGTH = 64;
    private static final Set<String> AUTH_COMMANDS = Set.of("login", "l", "register");
    private static final Set<String> COMMON_PASSWORDS = Set.of(
            "123456", "1234567", "12345678", "123456789", "1234567890", "111111", "000000", "123123",
            "654321", "666666", "121212", "112233", "password", "password1", "passw0rd", "qwerty",
            "qwerty123", "qwertyuiop", "asdfgh", "zxcvbn", "abc123", "iloveyou", "minecraft", "letmein");

    /** A connected player who has not logged in yet. */
    private static final class Pending {
        final String ip;
        final boolean localhost;
        final long joinedAt = System.currentTimeMillis();
        double x;
        double y;
        double z;
        float yRot;
        float xRot;
        int attempts;
        boolean busy;
        boolean kicked;
        long nextReminder;

        Pending(String ip, boolean localhost) {
            this.ip = ip;
            this.localhost = localhost;
        }
    }

    private final Map<UUID, Pending> pending = new ConcurrentHashMap<>();
    private final Set<UUID> changingPassword = ConcurrentHashMap.newKeySet();
    private final Map<String, Deque<Long>> failures = new HashMap<>();
    private final Map<String, Long> lockedUntil = new HashMap<>();
    private MinecraftServer server;
    private AccountStore store;
    private ExecutorService worker;
    private boolean active;
    private boolean broken;

    void start(MinecraftServer server) {
        this.server = server;
        active = !server.usesAuthentication();
        if (!active) {
            ServerAuth.LOG.info("online-mode is on, so Mojang already checks every player; Server Auth stays off");
            return;
        }
        worker = Executors.newSingleThreadExecutor(task -> {
            Thread thread = new Thread(task, "ServerAuth worker");
            thread.setDaemon(true);
            return thread;
        });
        store = new AccountStore(FMLPaths.GAMEDIR.get().resolve("serverauth").resolve("accounts.json"));
        try {
            store.load();
            broken = false;
            ServerAuth.LOG.info("Server Auth is on: {} accounts in {}", store.size(), store.file());
        } catch (Exception e) {
            broken = true;
            ServerAuth.LOG.error("Could not read {}. Nobody can join until it is fixed or deleted", store.file(), e);
        }
    }

    void stop() {
        if (worker != null) {
            worker.shutdown();
            try {
                worker.awaitTermination(10, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            worker = null;
        }
        pending.clear();
        changingPassword.clear();
        active = false;
    }

    boolean isPending(Player player) {
        return active && pending.containsKey(player.getUUID());
    }

    static boolean isAuthCommand(String input) {
        String command = input.startsWith("/") ? input.substring(1) : input;
        int space = command.indexOf(' ');
        return AUTH_COMMANDS.contains((space < 0 ? command : command.substring(0, space)).toLowerCase(Locale.ROOT));
    }

    // ---- joining, leaving, ticking ----

    void onJoin(ServerPlayer player) {
        if (!active) {
            return;
        }
        SocketAddress address = player.connection.connection.getRemoteAddress();
        String ip = address instanceof InetSocketAddress inet && inet.getAddress() != null
                ? inet.getAddress().getHostAddress() : String.valueOf(address);
        boolean localhost = address instanceof InetSocketAddress inet && inet.getAddress() != null
                && inet.getAddress().isLoopbackAddress();
        Pending state = new Pending(ip, localhost);
        // Lock the player down first, so nothing slips through before a kick takes effect.
        pending.put(player.getUUID(), state);
        hold(player, state);

        String name = player.getGameProfile().getName();
        Account account = broken ? null : store.get(name);
        long lockedFor = lockRemainingMillis(ip);
        if (broken) {
            kick(player, "The login system is not working. Tell the server owner.");
        } else if (lockedFor > 0) {
            kick(player, "Too many wrong passwords from your address. Try again in " + minutes(lockedFor) + ".");
        } else if (account != null && !account.name().equals(name)) {
            kick(player, "This name is registered as \"" + account.name() + "\". Join with exactly that name.");
        } else {
            remind(player, state);
        }
    }

    void onLeave(Player player) {
        pending.remove(player.getUUID());
    }

    /** A pending player who rejoined while dead and pressed respawn gets a new player object. */
    void onRespawn(ServerPlayer player) {
        Pending state = pending.get(player.getUUID());
        if (state != null) {
            hold(player, state);
        }
    }

    void tick(ServerPlayer player) {
        Pending state = pending.get(player.getUUID());
        if (state == null || state.kicked) {
            return;
        }
        long now = System.currentTimeMillis();
        if (now - state.joinedAt > AuthConfig.TIMEOUT_SECONDS.get() * 1000L) {
            kick(player, "You did not log in in time. Reconnect and use /login <password>.");
            return;
        }
        if (player.isAlive() && player.distanceToSqr(state.x, state.y, state.z) > 0.0025) {
            player.connection.teleport(state.x, state.y, state.z, state.yRot, state.xRot);
        }
        player.fallDistance = 0;
        if (now >= state.nextReminder) {
            remind(player, state);
        }
    }

    void remind(ServerPlayer player) {
        Pending state = pending.get(player.getUUID());
        if (state != null && !state.kicked) {
            remind(player, state);
        }
    }

    private void remind(ServerPlayer player, Pending state) {
        long secondsLeft = Math.max(0, AuthConfig.TIMEOUT_SECONDS.get() - (System.currentTimeMillis() - state.joinedAt) / 1000);
        boolean registered = store.get(player.getGameProfile().getName()) != null;
        String text = registered ? "Log in with /login <password>" : "Choose a password: /register <password> <password>";
        tell(player, ChatFormatting.GOLD, text + "  (" + secondsLeft + "s left)");
        state.nextReminder = System.currentTimeMillis() + REMINDER_MILLIS;
    }

    // ---- commands ----

    int register(ServerPlayer player, String input) {
        Pending state = pending.get(player.getUUID());
        if (!active) {
            return notNeeded(player);
        }
        if (state == null) {
            tell(player, ChatFormatting.RED, "You are already logged in. Use /changepassword <old> <new> to change it.");
            return 0;
        }
        if (state.busy) {
            return pleaseWait(player);
        }
        String name = player.getGameProfile().getName();
        if (store.get(name) != null) {
            tell(player, ChatFormatting.RED, "This name already has a password. Use /login <password>");
            return 0;
        }
        String[] parts = input.trim().split("\\s+");
        if (parts.length != 2) {
            tell(player, ChatFormatting.RED, "Usage: /register <password> <password>  (no spaces in the password)");
            return 0;
        }
        if (!parts[0].equals(parts[1])) {
            tell(player, ChatFormatting.RED, "The two passwords are different. Type the same password twice.");
            return 0;
        }
        String problem = passwordProblem(parts[0], name);
        if (problem != null) {
            tell(player, ChatFormatting.RED, problem);
            return 0;
        }
        state.busy = true;
        UUID id = player.getUUID();
        String password = parts[0];
        int iterations = AuthConfig.HASH_ITERATIONS.get();
        worker.execute(() -> {
            String hash = PasswordHasher.hash(password, iterations);
            server.execute(() -> finishRegister(id, hash));
        });
        return 1;
    }

    private void finishRegister(UUID id, String hash) {
        Pending state = pending.get(id);
        ServerPlayer player = server.getPlayerList().getPlayer(id);
        if (state == null || player == null || state.kicked) {
            return;
        }
        state.busy = false;
        String name = player.getGameProfile().getName();
        if (store.get(name) != null) {
            tell(player, ChatFormatting.RED, "This name already has a password. Use /login <password>");
            return;
        }
        long now = System.currentTimeMillis();
        store.put(new Account(name, hash, now, now));
        save();
        ServerAuth.LOG.info("{} registered from {}", name, state.ip);
        authenticate(player, "Password saved and you are logged in. Use the same password next time.");
    }

    int login(ServerPlayer player, String password) {
        Pending state = pending.get(player.getUUID());
        if (!active) {
            return notNeeded(player);
        }
        if (state == null) {
            tell(player, ChatFormatting.GREEN, "You are already logged in.");
            return 0;
        }
        if (state.busy) {
            return pleaseWait(player);
        }
        Account account = store.get(player.getGameProfile().getName());
        if (account == null) {
            tell(player, ChatFormatting.RED, "You have no password yet. Use /register <password> <password>");
            return 0;
        }
        state.busy = true;
        UUID id = player.getUUID();
        String hash = account.hash();
        int iterations = AuthConfig.HASH_ITERATIONS.get();
        worker.execute(() -> {
            boolean correct = PasswordHasher.verify(password.trim(), hash);
            String upgraded = correct && PasswordHasher.needsRehash(hash, iterations)
                    ? PasswordHasher.hash(password.trim(), iterations) : null;
            server.execute(() -> finishLogin(id, hash, correct, upgraded));
        });
        return 1;
    }

    private void finishLogin(UUID id, String checkedHash, boolean correct, String upgradedHash) {
        Pending state = pending.get(id);
        ServerPlayer player = server.getPlayerList().getPlayer(id);
        if (state == null || player == null || state.kicked) {
            return;
        }
        state.busy = false;
        String name = player.getGameProfile().getName();
        Account account = store.get(name);
        if (account == null || !account.hash().equals(checkedHash)) {
            tell(player, ChatFormatting.RED, "Your password was changed meanwhile. Please try again.");
            return;
        }
        if (correct) {
            String hash = upgradedHash != null ? upgradedHash : account.hash();
            store.put(new Account(account.name(), hash, account.registeredAt(), System.currentTimeMillis()));
            save();
            ServerAuth.LOG.info("{} logged in from {}", name, state.ip);
            authenticate(player, "Logged in. Have fun!");
            return;
        }
        state.attempts++;
        ServerAuth.LOG.warn("Wrong password for {} from {} (attempt {})", name, state.ip, state.attempts);
        if (recordFailure(state)) {
            kick(player, "Too many wrong passwords from your address. Try again in "
                    + minutes(lockRemainingMillis(state.ip)) + ".");
            return;
        }
        int left = AuthConfig.MAX_ATTEMPTS.get() - state.attempts;
        if (left <= 0) {
            kick(player, "Too many wrong passwords.");
        } else {
            tell(player, ChatFormatting.RED, "Wrong password. " + left + (left == 1 ? " try" : " tries") + " left.");
        }
    }

    int changePassword(ServerPlayer player, String input) {
        UUID id = player.getUUID();
        if (!active) {
            return notNeeded(player);
        }
        if (pending.containsKey(id)) {
            tell(player, ChatFormatting.RED, "Log in first with /login <password>");
            return 0;
        }
        String[] parts = input.trim().split("\\s+");
        if (parts.length != 2) {
            tell(player, ChatFormatting.RED, "Usage: /changepassword <old password> <new password>");
            return 0;
        }
        String name = player.getGameProfile().getName();
        Account account = store.get(name);
        if (account == null) {
            tell(player, ChatFormatting.RED, "You have no password. Reconnect to choose one.");
            return 0;
        }
        String problem = passwordProblem(parts[1], name);
        if (problem != null) {
            tell(player, ChatFormatting.RED, problem);
            return 0;
        }
        if (!changingPassword.add(id)) {
            return pleaseWait(player);
        }
        String oldPassword = parts[0];
        String newPassword = parts[1];
        String oldHash = account.hash();
        int iterations = AuthConfig.HASH_ITERATIONS.get();
        worker.execute(() -> {
            String newHash = PasswordHasher.verify(oldPassword, oldHash) ? PasswordHasher.hash(newPassword, iterations) : null;
            server.execute(() -> finishChangePassword(id, name, oldHash, newHash));
        });
        return 1;
    }

    private void finishChangePassword(UUID id, String name, String oldHash, String newHash) {
        changingPassword.remove(id);
        ServerPlayer player = server.getPlayerList().getPlayer(id);
        Account account = store.get(name);
        if (newHash == null || account == null || !account.hash().equals(oldHash)) {
            if (player != null) {
                tell(player, ChatFormatting.RED, "Your old password is not correct.");
            }
            return;
        }
        store.put(new Account(account.name(), newHash, account.registeredAt(), account.lastLoginAt()));
        save();
        ServerAuth.LOG.info("{} changed their password", name);
        if (player != null) {
            tell(player, ChatFormatting.GREEN, "Password changed.");
        }
    }

    int unregister(CommandSourceStack source, String name) {
        if (!active) {
            source.sendFailure(Component.literal("Server Auth is off because online-mode is on."));
            return 0;
        }
        if (!store.remove(name)) {
            source.sendFailure(Component.literal(name + " has no password."));
            return 0;
        }
        save();
        ServerPlayer online = server.getPlayerList().getPlayerByName(name);
        if (online != null && pending.containsKey(online.getUUID())) {
            tell(online, ChatFormatting.GOLD, "Your password was reset. Choose a new one with /register <password> <password>");
        } else if (online != null) {
            kick(online, "An admin reset your password. Reconnect and choose a new one with /register.");
        }
        source.sendSuccess(() -> Component.literal("Removed the password of " + name + ". They can /register again."), true);
        return 1;
    }

    // ---- helpers ----

    private void authenticate(ServerPlayer player, String message) {
        pending.remove(player.getUUID());
        restoreLocation(player);
        restoreInventory(player);
        CompoundTag data = persisted(player);
        if (data.getBoolean(BLIND_KEY)) {
            player.removeEffect(MobEffects.BLINDNESS);
            data.remove(BLIND_KEY);
        }
        tell(player, ChatFormatting.GREEN, message);
    }

    private void hold(ServerPlayer player, Pending state) {
        if (player.isPassenger()) {
            player.stopRiding();
        }
        if (AuthConfig.HIDE_INVENTORY.get()) {
            hideInventory(player);
        }
        if (AuthConfig.HIDE_LOCATION.get() && player.isAlive()) {
            moveToSpawn(player);
        }
        state.x = player.getX();
        state.y = player.getY();
        state.z = player.getZ();
        state.yRot = player.getYRot();
        state.xRot = player.getXRot();
        int ticks = (AuthConfig.TIMEOUT_SECONDS.get() + 5) * 20;
        player.addEffect(new MobEffectInstance(MobEffects.BLINDNESS, ticks, 0, false, false, false));
        persisted(player).putBoolean(BLIND_KEY, true);
    }

    /** Stores the inventory in the player's saved data, so it survives crashes and disconnects. */
    private static void hideInventory(ServerPlayer player) {
        Inventory inventory = player.getInventory();
        CompoundTag data = persisted(player);
        if (inventory.isEmpty() || data.contains(INVENTORY_KEY)) {
            // Nothing to hide, or an older copy is still waiting: never overwrite it.
            return;
        }
        data.put(INVENTORY_KEY, inventory.save(new ListTag()));
        inventory.clearContent();
        player.inventoryMenu.broadcastChanges();
    }

    private static void restoreInventory(ServerPlayer player) {
        CompoundTag data = persisted(player);
        if (!data.contains(INVENTORY_KEY, Tag.TAG_LIST)) {
            return;
        }
        ListTag saved = data.getList(INVENTORY_KEY, Tag.TAG_COMPOUND);
        data.remove(INVENTORY_KEY);
        Inventory inventory = player.getInventory();
        for (int i = 0; i < saved.size(); i++) {
            CompoundTag entry = saved.getCompound(i);
            ItemStack stack = ItemStack.of(entry);
            if (stack.isEmpty()) {
                continue;
            }
            // Same slot numbers as Inventory.save(): 0-35 main, 100+ armor, 150+ offhand.
            int slot = entry.getByte("Slot") & 255;
            NonNullList<ItemStack> list = null;
            int index = -1;
            if (slot < inventory.items.size()) {
                list = inventory.items;
                index = slot;
            } else if (slot >= 100 && slot < 100 + inventory.armor.size()) {
                list = inventory.armor;
                index = slot - 100;
            } else if (slot >= 150 && slot < 150 + inventory.offhand.size()) {
                list = inventory.offhand;
                index = slot - 150;
            }
            if (list != null && list.get(index).isEmpty()) {
                list.set(index, stack);
            } else {
                inventory.add(stack);
                if (!stack.isEmpty()) {
                    player.drop(stack, false);
                }
            }
        }
        player.inventoryMenu.broadcastChanges();
    }

    private void moveToSpawn(ServerPlayer player) {
        CompoundTag data = persisted(player);
        if (!data.contains(LOCATION_KEY)) {
            CompoundTag location = new CompoundTag();
            location.putString("dimension", player.level().dimension().location().toString());
            location.putDouble("x", player.getX());
            location.putDouble("y", player.getY());
            location.putDouble("z", player.getZ());
            location.putFloat("yRot", player.getYRot());
            location.putFloat("xRot", player.getXRot());
            data.put(LOCATION_KEY, location);
        }
        ServerLevel overworld = server.overworld();
        BlockPos spawn = overworld.getHeightmapPos(Heightmap.Types.MOTION_BLOCKING, overworld.getSharedSpawnPos());
        player.teleportTo(overworld, spawn.getX() + 0.5, spawn.getY(), spawn.getZ() + 0.5, player.getYRot(), player.getXRot());
    }

    private void restoreLocation(ServerPlayer player) {
        CompoundTag data = persisted(player);
        if (!data.contains(LOCATION_KEY, Tag.TAG_COMPOUND)) {
            return;
        }
        CompoundTag location = data.getCompound(LOCATION_KEY);
        data.remove(LOCATION_KEY);
        ResourceLocation id = ResourceLocation.tryParse(location.getString("dimension"));
        ServerLevel level = id == null ? null : server.getLevel(ResourceKey.create(Registries.DIMENSION, id));
        if (level == null) {
            level = server.overworld(); // the dimension's mod was removed
        }
        player.teleportTo(level, location.getDouble("x"), location.getDouble("y"), location.getDouble("z"),
                location.getFloat("yRot"), location.getFloat("xRot"));
        player.fallDistance = 0;
    }

    private static CompoundTag persisted(Player player) {
        // PlayerPersisted is the part of Forge's player data that is kept through death and respawn.
        CompoundTag data = player.getPersistentData();
        if (!data.contains(Player.PERSISTED_NBT_TAG, Tag.TAG_COMPOUND)) {
            data.put(Player.PERSISTED_NBT_TAG, new CompoundTag());
        }
        return data.getCompound(Player.PERSISTED_NBT_TAG);
    }

    private long lockRemainingMillis(String ip) {
        Long until = lockedUntil.get(ip);
        if (until == null) {
            return 0;
        }
        long left = until - System.currentTimeMillis();
        if (left <= 0) {
            lockedUntil.remove(ip);
            return 0;
        }
        return left;
    }

    /** Counts a wrong password; returns true when the address is now locked out. */
    private boolean recordFailure(Pending state) {
        int max = AuthConfig.IP_MAX_FAILURES.get();
        if (max == 0 || state.localhost && AuthConfig.IP_EXEMPT_LOCALHOST.get()) {
            return false;
        }
        long now = System.currentTimeMillis();
        long window = AuthConfig.IP_WINDOW_MINUTES.get() * 60_000L;
        Deque<Long> times = failures.computeIfAbsent(state.ip, ip -> new ArrayDeque<>());
        times.addLast(now);
        while (now - times.peekFirst() > window) {
            times.removeFirst();
        }
        if (times.size() < max) {
            return false;
        }
        failures.remove(state.ip);
        lockedUntil.put(state.ip, now + AuthConfig.IP_LOCK_MINUTES.get() * 60_000L);
        ServerAuth.LOG.warn("Locked out {} for {} minutes after {} wrong passwords", state.ip, AuthConfig.IP_LOCK_MINUTES.get(), max);
        return true;
    }

    private static String passwordProblem(String password, String name) {
        int min = AuthConfig.MIN_PASSWORD_LENGTH.get();
        if (password.length() < min) {
            return "Use at least " + min + " characters.";
        }
        if (password.length() > MAX_PASSWORD_LENGTH) {
            return "Use at most " + MAX_PASSWORD_LENGTH + " characters.";
        }
        String lower = password.toLowerCase(Locale.ROOT);
        if (name.length() >= 3 && lower.contains(name.toLowerCase(Locale.ROOT))) {
            return "Don't put your name in your password.";
        }
        if (COMMON_PASSWORDS.contains(lower) || password.chars().distinct().count() < 3) {
            return "That password is too easy to guess. Pick another one.";
        }
        return null;
    }

    private void save() {
        worker.execute(store.saveTask());
    }

    /** Disconnects on the next tick (never in the middle of the current one). */
    private void kick(ServerPlayer player, String reason) {
        Pending state = pending.get(player.getUUID());
        if (state != null) {
            if (state.kicked) {
                return;
            }
            state.kicked = true;
        }
        Component message = Component.literal(reason);
        server.tell(new TickTask(server.getTickCount(), () -> player.connection.disconnect(message)));
    }

    private static String minutes(long millis) {
        long minutes = Math.max(1, (millis + 59_999) / 60_000);
        return minutes + (minutes == 1 ? " minute" : " minutes");
    }

    private static int notNeeded(ServerPlayer player) {
        tell(player, ChatFormatting.GRAY, "This server checks accounts with Mojang (online-mode), so no password is needed.");
        return 0;
    }

    private static int pleaseWait(ServerPlayer player) {
        tell(player, ChatFormatting.GRAY, "Checking your password, please wait...");
        return 0;
    }

    private static void tell(ServerPlayer player, ChatFormatting color, String text) {
        player.sendSystemMessage(Component.literal(text).withStyle(color));
    }
}
