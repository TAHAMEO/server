package serverauth;

import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraftforge.event.CommandEvent;
import net.minecraftforge.event.RegisterCommandsEvent;
import net.minecraftforge.event.ServerChatEvent;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.entity.EntityMountEvent;
import net.minecraftforge.event.entity.item.ItemTossEvent;
import net.minecraftforge.event.entity.living.LivingAttackEvent;
import net.minecraftforge.event.entity.living.LivingChangeTargetEvent;
import net.minecraftforge.event.entity.player.AttackEntityEvent;
import net.minecraftforge.event.entity.player.EntityItemPickupEvent;
import net.minecraftforge.event.entity.player.PlayerContainerEvent;
import net.minecraftforge.event.entity.player.PlayerEvent;
import net.minecraftforge.event.entity.player.PlayerInteractEvent;
import net.minecraftforge.event.entity.player.PlayerXpEvent;
import net.minecraftforge.event.level.BlockEvent;
import net.minecraftforge.event.server.ServerStartingEvent;
import net.minecraftforge.event.server.ServerStoppedEvent;
import net.minecraftforge.eventbus.api.Event;
import net.minecraftforge.eventbus.api.EventPriority;
import net.minecraftforge.eventbus.api.SubscribeEvent;

/** Until a player logs in they cannot chat, run commands, move, touch, take damage or be targeted. */
final class AuthEvents {
    private final AuthManager auth;

    AuthEvents(AuthManager auth) {
        this.auth = auth;
    }

    @SubscribeEvent
    public void onServerStarting(ServerStartingEvent event) {
        auth.start(event.getServer());
    }

    @SubscribeEvent
    public void onServerStopped(ServerStoppedEvent event) {
        auth.stop();
    }

    @SubscribeEvent
    public void onRegisterCommands(RegisterCommandsEvent event) {
        AuthCommands.register(event.getDispatcher(), auth);
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onLoggedIn(PlayerEvent.PlayerLoggedInEvent event) {
        if (event.getEntity() instanceof ServerPlayer player) {
            auth.onJoin(player);
        }
    }

    @SubscribeEvent
    public void onLoggedOut(PlayerEvent.PlayerLoggedOutEvent event) {
        auth.onLeave(event.getEntity());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onRespawn(PlayerEvent.PlayerRespawnEvent event) {
        if (event.getEntity() instanceof ServerPlayer player) {
            auth.onRespawn(player);
        }
    }

    @SubscribeEvent
    public void onPlayerTick(TickEvent.PlayerTickEvent event) {
        if (event.phase == TickEvent.Phase.END && event.player instanceof ServerPlayer player) {
            auth.tick(player);
        }
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onChat(ServerChatEvent event) {
        if (auth.isPending(event.getPlayer())) {
            event.setCanceled(true);
            auth.remind(event.getPlayer());
        }
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onCommand(CommandEvent event) {
        if (event.getParseResults().getContext().getSource().getEntity() instanceof ServerPlayer player
                && auth.isPending(player)
                && !AuthManager.isAuthCommand(event.getParseResults().getReader().getString())) {
            event.setCanceled(true);
            auth.remind(player);
        }
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onRightClickBlock(PlayerInteractEvent.RightClickBlock event) {
        block(event, event.getEntity());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onRightClickItem(PlayerInteractEvent.RightClickItem event) {
        block(event, event.getEntity());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onLeftClickBlock(PlayerInteractEvent.LeftClickBlock event) {
        block(event, event.getEntity());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onEntityInteract(PlayerInteractEvent.EntityInteract event) {
        block(event, event.getEntity());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onEntityInteractSpecific(PlayerInteractEvent.EntityInteractSpecific event) {
        block(event, event.getEntity());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onBreak(BlockEvent.BreakEvent event) {
        block(event, event.getPlayer());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onPlace(BlockEvent.EntityPlaceEvent event) {
        if (event.getEntity() instanceof Player player) {
            block(event, player);
        }
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onAttackEntity(AttackEntityEvent event) {
        block(event, event.getEntity());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onLivingAttack(LivingAttackEvent event) {
        // Players who are still logging in can neither be hurt nor hurt anything.
        if (event.getEntity() instanceof Player victim && auth.isPending(victim)
                || event.getSource().getEntity() instanceof Player attacker && auth.isPending(attacker)) {
            event.setCanceled(true);
        }
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onChangeTarget(LivingChangeTargetEvent event) {
        if (event.getNewTarget() instanceof Player target && auth.isPending(target)) {
            event.setCanceled(true);
        }
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onToss(ItemTossEvent event) {
        Player player = event.getPlayer();
        if (auth.isPending(player)) {
            // A cancelled toss deletes the item, so put it back first.
            ItemStack rest = event.getEntity().getItem().copy();
            player.getInventory().add(rest);
            if (rest.isEmpty()) {
                event.setCanceled(true);
            } else {
                event.getEntity().setItem(rest);
            }
        }
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onPickupItem(EntityItemPickupEvent event) {
        block(event, event.getEntity());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onPickupXp(PlayerXpEvent.PickupXp event) {
        block(event, event.getEntity());
    }

    @SubscribeEvent(priority = EventPriority.HIGHEST)
    public void onMount(EntityMountEvent event) {
        if (event.isMounting() && event.getEntityMounting() instanceof Player player) {
            block(event, player);
        }
    }

    @SubscribeEvent
    public void onContainerOpen(PlayerContainerEvent.Open event) {
        if (event.getEntity() instanceof ServerPlayer player && auth.isPending(player)) {
            player.closeContainer();
        }
    }

    private void block(Event event, Player player) {
        if (player != null && auth.isPending(player)) {
            event.setCanceled(true);
        }
    }
}
