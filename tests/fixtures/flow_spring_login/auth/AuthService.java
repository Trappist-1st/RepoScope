package auth;

import user.UserRepository;

public class AuthService {

    private final UserRepository userRepository = new UserRepository();

    public String login(String username, String password) {
        String user = userRepository.findByUsername(username);
        if (user == null) {
            return "unauthorized";
        }
        return "token-for-" + user;
    }
}
