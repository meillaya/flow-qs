EXPERIMENTS := experiments/pod-kill-checkout.yaml experiments/network-delay-payment.yaml experiments/cpu-stress-catalog.yaml experiments/dns-chaos-inventory.yaml
APPLY_FLAGS := $(foreach f,$(EXPERIMENTS),-f $(f))

.PHONY: chaos-all dry-run clean

chaos-all:
	@echo "Applying PodChaos: pod-kill-checkout"
	kubectl apply -f experiments/pod-kill-checkout.yaml
	sleep 10
	@echo "Applying NetworkChaos: network-delay-payment"
	kubectl apply -f experiments/network-delay-payment.yaml
	sleep 10
	@echo "Applying StressChaos: cpu-stress-catalog"
	kubectl apply -f experiments/cpu-stress-catalog.yaml
	sleep 10
	@echo "Applying DNSChaos: dns-chaos-inventory"
	kubectl apply -f experiments/dns-chaos-inventory.yaml

dry-run:
	kubectl apply --dry-run=server $(APPLY_FLAGS)

clean:
	-kubectl delete $(APPLY_FLAGS)
