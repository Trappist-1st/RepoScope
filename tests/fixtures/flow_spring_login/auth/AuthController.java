package auth;

import user.UserRepository;
import auth.AuthService;

/**
 * Minimal Spring-style login entry (fixture for Flow Trace).
 */
public class AuthController {

    private final AuthService authService = new AuthService();

    public String login(String username, String password) {
        return authService.login(username, password);
    }

    public String health() {
        return "ok";
    }
}
