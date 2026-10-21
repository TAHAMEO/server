import java.io.File;
import net.minecraftforge.srgutils.IMappingFile;

/** Chains Mojang (named -> obfuscated) with MCP (obfuscated -> SRG) into named -> SRG. */
public class Combine {
    public static void main(String[] args) throws Exception {
        IMappingFile mojang = IMappingFile.load(new File(args[0]));
        IMappingFile srg = IMappingFile.load(new File(args[1]));
        mojang.chain(srg).write(new File(args[2]).getAbsoluteFile().toPath(), IMappingFile.Format.TSRG2, false);
    }
}
