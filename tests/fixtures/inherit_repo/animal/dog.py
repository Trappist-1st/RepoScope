from animal.base import Animal


class Dog(Animal):
    def speak(self) -> str:
        return "woof"
