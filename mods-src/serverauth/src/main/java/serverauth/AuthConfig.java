package serverauth;

import net.minecraftforge.common.ForgeConfigSpec;

/** Settings in config/serverauth.toml. */
final class AuthConfig {
    static final ForgeConfigSpec SPEC;
    static final ForgeConfigSpec.IntValue TIMEOUT_SECONDS;
    static final ForgeConfigSpec.IntValue MAX_ATTEMPTS;
    static final ForgeConfigSpec.IntValue MIN_PASSWORD_LENGTH;
    static final ForgeConfigSpec.BooleanValue HIDE_INVENTORY;
    static final ForgeConfigSpec.BooleanValue HIDE_LOCATION;
    static final ForgeConfigSpec.IntValue IP_MAX_FAILURES;
    static final ForgeConfigSpec.IntValue IP_WINDOW_MINUTES;
    static final ForgeConfigSpec.IntValue IP_LOCK_MINUTES;
    static final ForgeConfigSpec.BooleanValue IP_EXEMPT_LOCALHOST;
    static final ForgeConfigSpec.IntValue HASH_ITERATIONS;

    static {
        ForgeConfigSpec.Builder b = new ForgeConfigSpec.Builder();

        b.push("login");
        TIMEOUT_SECONDS = b.comment("Kick players who have not logged in after this many seconds.")
                .defineInRange("timeoutSeconds", 60, 15, 600);
        MAX_ATTEMPTS = b.comment("Wrong passwords allowed per connection before the player is kicked.")
                .defineInRange("maxAttempts", 3, 1, 10);
        MIN_PASSWORD_LENGTH = b.comment("Shortest password that /register and /changepassword accept.")
                .defineInRange("minPasswordLength", 6, 4, 64);
        b.pop();

        b.push("protection");
        HIDE_INVENTORY = b.comment("Hide the inventory until the player logs in, so someone using their name cannot see or use their items.")
                .define("hideInventory", true);
        HIDE_LOCATION = b.comment("Keep players at world spawn until they log in, so someone using their name cannot find out where they logged off.")
                .define("hideLocation", true);
        b.pop();

        b.push("ipLockout");
        IP_MAX_FAILURES = b.comment(
                        "Wrong passwords from one IP address (within windowMinutes) before that address is locked out. 0 turns this off.",
                        "Set it to 0 if all players reach the server through one address, for example a tunnel such as playit.gg.")
                .defineInRange("maxFailures", 6, 0, 100);
        IP_WINDOW_MINUTES = b.defineInRange("windowMinutes", 10, 1, 1440);
        IP_LOCK_MINUTES = b.comment("How long a locked-out address has to wait.")
                .defineInRange("lockMinutes", 15, 1, 1440);
        IP_EXEMPT_LOCALHOST = b.comment("Never lock out 127.0.0.1 / ::1 (someone playing on the server machine itself).")
                .define("exemptLocalhost", true);
        b.pop();

        b.push("hashing");
        HASH_ITERATIONS = b.comment("PBKDF2-HMAC-SHA512 iterations for new passwords. Older hashes are upgraded at the next login.")
                .defineInRange("iterations", 210_000, 100_000, 2_000_000);
        b.pop();

        SPEC = b.build();
    }

    private AuthConfig() {
    }
}
