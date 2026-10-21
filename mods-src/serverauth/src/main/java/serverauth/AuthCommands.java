package serverauth;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.tree.LiteralCommandNode;
import net.minecraft.ChatFormatting;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;

/** /register, /login (/l), /changepassword and the admin command /auth unregister. */
final class AuthCommands {
    static void register(CommandDispatcher<CommandSourceStack> dispatcher, AuthManager auth) {
        dispatcher.register(Commands.literal("register")
                .executes(c -> usage(c.getSource(), "/register <password> <password>"))
                .then(Commands.argument("passwords", StringArgumentType.greedyString())
                        .executes(c -> auth.register(c.getSource().getPlayerOrException(),
                                StringArgumentType.getString(c, "passwords")))));

        LiteralCommandNode<CommandSourceStack> login = dispatcher.register(Commands.literal("login")
                .executes(c -> usage(c.getSource(), "/login <password>"))
                .then(Commands.argument("password", StringArgumentType.greedyString())
                        .executes(c -> auth.login(c.getSource().getPlayerOrException(),
                                StringArgumentType.getString(c, "password")))));
        dispatcher.register(Commands.literal("l")
                .executes(c -> usage(c.getSource(), "/login <password>"))
                .redirect(login));

        dispatcher.register(Commands.literal("changepassword")
                .executes(c -> usage(c.getSource(), "/changepassword <old password> <new password>"))
                .then(Commands.argument("passwords", StringArgumentType.greedyString())
                        .executes(c -> auth.changePassword(c.getSource().getPlayerOrException(),
                                StringArgumentType.getString(c, "passwords")))));

        dispatcher.register(Commands.literal("auth")
                .requires(source -> source.hasPermission(3))
                .then(Commands.literal("unregister")
                        .then(Commands.argument("player", StringArgumentType.word())
                                .executes(c -> auth.unregister(c.getSource(),
                                        StringArgumentType.getString(c, "player"))))));
    }

    private static int usage(CommandSourceStack source, String usage) {
        source.sendFailure(Component.literal("Usage: " + usage).withStyle(ChatFormatting.RED));
        return 0;
    }

    private AuthCommands() {
    }
}
