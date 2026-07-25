package user;

public class UserRepository {

    public String findByUsername(String username) {
        if (username == null || username.isEmpty()) {
            return null;
        }
        return username;
    }
}
