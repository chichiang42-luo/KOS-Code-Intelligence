package payment

func RunPayment() bool {
	processor := &Processor{}
	return processor.Verify("order-1")
}
