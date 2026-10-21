package serverauth;

import com.mojang.logging.LogUtils;
import net.minecraftforge.api.distmarker.Dist;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.ModLoadingContext;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.config.ModConfig;
import net.minecraftforge.fml.loading.FMLEnvironment;
import org.slf4j.Logger;

/**
 * Password login (/register, /login) for offline-mode servers. Runs only on the
 * dedicated server; players do not need to install it.
 */
@Mod(ServerAuth.MOD_ID)
public final class ServerAuth {
    public static final String MOD_ID = "serverauth";
    static final Logger LOG = LogUtils.getLogger();

    @SuppressWarnings("removal") // ModLoadingContext.get() is the supported way on Forge 47 (1.20.1)
    public ServerAuth() {
        if (FMLEnvironment.dist != Dist.DEDICATED_SERVER) {
            LOG.info("Server Auth only works on dedicated servers; it stays off here");
            return;
        }
        ModLoadingContext.get().registerConfig(ModConfig.Type.COMMON, AuthConfig.SPEC, "serverauth.toml");
        MinecraftForge.EVENT_BUS.register(new AuthEvents(new AuthManager()));
    }
}
