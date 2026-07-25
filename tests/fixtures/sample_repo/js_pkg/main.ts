import { add, Calculator } from "./util";

export function main(): number {
  const calc = new Calculator();
  return add(1, calc.multiply(2, 3));
}
