package serverauth;

import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/** Salted PBKDF2-HMAC-SHA512, stored as {@code pbkdf2_sha512$<iterations>$<salt>$<hash>} (Base64). */
final class PasswordHasher {
    private static final String SCHEME = "pbkdf2_sha512";
    private static final String ALGORITHM = "PBKDF2WithHmacSHA512";
    private static final int SALT_BYTES = 16;
    private static final int KEY_BITS = 512;
    private static final SecureRandom RANDOM = new SecureRandom();

    static String hash(String password, int iterations) {
        byte[] salt = new byte[SALT_BYTES];
        RANDOM.nextBytes(salt);
        Base64.Encoder base64 = Base64.getEncoder().withoutPadding();
        return SCHEME + "$" + iterations + "$" + base64.encodeToString(salt) + "$"
                + base64.encodeToString(derive(password, salt, iterations));
    }

    static boolean verify(String password, String stored) {
        String[] parts = stored.split("\\$");
        if (parts.length != 4 || !parts[0].equals(SCHEME)) {
            return false;
        }
        try {
            int iterations = Integer.parseInt(parts[1]);
            byte[] salt = Base64.getDecoder().decode(parts[2]);
            byte[] expected = Base64.getDecoder().decode(parts[3]);
            return MessageDigest.isEqual(expected, derive(password, salt, iterations));
        } catch (IllegalArgumentException e) {
            return false;
        }
    }

    /** True when the stored hash is weaker than what new passwords get. */
    static boolean needsRehash(String stored, int iterations) {
        String[] parts = stored.split("\\$");
        try {
            return parts.length != 4 || !parts[0].equals(SCHEME) || Integer.parseInt(parts[1]) < iterations;
        } catch (NumberFormatException e) {
            return true;
        }
    }

    private static byte[] derive(String password, byte[] salt, int iterations) {
        PBEKeySpec spec = new PBEKeySpec(password.toCharArray(), salt, iterations, KEY_BITS);
        try {
            return SecretKeyFactory.getInstance(ALGORITHM).generateSecret(spec).getEncoded();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(ALGORITHM + " is not available", e);
        } finally {
            spec.clearPassword();
        }
    }

    private PasswordHasher() {
    }
}
