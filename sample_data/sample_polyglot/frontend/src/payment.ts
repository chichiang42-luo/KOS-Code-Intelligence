export function auditPayment(orderId: string): boolean {
  return orderId.length > 0;
}

export function verifyPayment(orderId: string): boolean {
  return auditPayment(orderId);
}

export class BaseCheckout {
  submit(): boolean {
    return true;
  }
}

export class Checkout extends BaseCheckout {
  submit(): boolean {
    return verifyPayment("order-1");
  }
}
