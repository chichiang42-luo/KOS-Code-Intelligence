package payment

type Processor struct{}

func validate(orderID string) bool {
	return orderID != ""
}

func (p *Processor) Verify(orderID string) bool {
	return validate(orderID)
}
