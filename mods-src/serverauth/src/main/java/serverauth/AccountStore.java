package serverauth;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Password hashes in {@code serverauth/accounts.json}. The folder and file are
 * readable only by the user the server runs as.
 */
final class AccountStore {
    record Account(String name, String hash, long registeredAt, long lastLoginAt) {
    }

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    private final Path file;
    private final Map<String, Account> accounts = new ConcurrentHashMap<>();

    AccountStore(Path file) {
        this.file = file;
    }

    Path file() {
        return file;
    }

    void load() throws IOException {
        accounts.clear();
        if (!Files.exists(file)) {
            return;
        }
        JsonObject list = JsonParser.parseString(Files.readString(file)).getAsJsonObject().getAsJsonObject("accounts");
        for (String key : list.keySet()) {
            JsonObject a = list.getAsJsonObject(key);
            put(new Account(a.get("name").getAsString(), a.get("hash").getAsString(),
                    a.get("registeredAt").getAsLong(), a.get("lastLoginAt").getAsLong()));
        }
    }

    Account get(String name) {
        return accounts.get(key(name));
    }

    void put(Account account) {
        accounts.put(key(account.name()), account);
    }

    boolean remove(String name) {
        return accounts.remove(key(name)) != null;
    }

    int size() {
        return accounts.size();
    }

    /** Takes a snapshot now; the returned task writes it to disk (run it off the server thread). */
    Runnable saveTask() {
        JsonObject list = new JsonObject();
        new TreeMap<>(accounts).forEach((key, a) -> {
            JsonObject o = new JsonObject();
            o.addProperty("name", a.name());
            o.addProperty("hash", a.hash());
            o.addProperty("registeredAt", a.registeredAt());
            o.addProperty("lastLoginAt", a.lastLoginAt());
            list.add(key, o);
        });
        JsonObject root = new JsonObject();
        root.addProperty("version", 1);
        root.add("accounts", list);
        String json = GSON.toJson(root);
        return () -> write(json);
    }

    private void write(String json) {
        try {
            Path dir = file.getParent();
            Files.createDirectories(dir);
            restrict(dir, "rwx------");
            Path tmp = dir.resolve(file.getFileName() + ".tmp");
            Files.writeString(tmp, json);
            restrict(tmp, "rw-------");
            Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            ServerAuth.LOG.error("Could not save {}", file, e);
        }
    }

    private static void restrict(Path path, String permissions) throws IOException {
        try {
            Files.setPosixFilePermissions(path, PosixFilePermissions.fromString(permissions));
        } catch (UnsupportedOperationException e) {
            // Not a POSIX file system (Windows): nothing to restrict.
        }
    }

    private static String key(String name) {
        return name.toLowerCase(Locale.ROOT);
    }
}
