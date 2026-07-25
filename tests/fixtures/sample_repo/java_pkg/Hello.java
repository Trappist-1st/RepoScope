package java_pkg;

public class Hello {
    public static String greet(String name) {
        return "hello, " + name;
    }

    public String shout(String name) {
        return greet(name).toUpperCase();
    }
}
