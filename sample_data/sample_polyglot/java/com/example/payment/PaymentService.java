package com.example.payment;

public class PaymentService extends BaseService {
    public boolean process() {
        validate();
        Worker worker = new Worker();
        worker.run();
        return true;
    }

    private void validate() {}
}
