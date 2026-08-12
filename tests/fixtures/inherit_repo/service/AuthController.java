package service;

public class AuthController extends BaseController {
    private UserService userService;

    public String login(String id) {
        return userService.findUser(id);
    }
}
