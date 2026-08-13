import { verifyPayment as verifyOrder } from "./payment";

export function checkout(orderId: string): boolean {
  return verifyOrder(orderId);
}
