EXPERIMENTS := experiments/pod-kill-checkout.yaml experiments/network-delay-payment.yaml experiments/cpu-stress-catalog.yaml experiments/dns-chaos-inventory.yaml
APPLY_FLAGS := $(foreach f,$(EXPERIMENTS),-f $(f))

.PHONY: chaos-all dry-run clean verify-schedules verify-network-delay-measurement

chaos-all:
	@echo "Applying scheduled PodChaos workflow: pod-kill-checkout"
	kubectl apply -f experiments/pod-kill-checkout.yaml
	sleep 10
	@echo "Applying scheduled NetworkChaos: network-delay-payment"
	kubectl apply -f experiments/network-delay-payment.yaml
	sleep 10
	@echo "Applying scheduled StressChaos: cpu-stress-catalog"
	kubectl apply -f experiments/cpu-stress-catalog.yaml
	sleep 10
	@echo "Applying scheduled DNSChaos: dns-chaos-inventory"
	kubectl apply -f experiments/dns-chaos-inventory.yaml

dry-run:
	kubectl apply --dry-run=server $(APPLY_FLAGS)

clean:
	-kubectl delete $(APPLY_FLAGS)

verify-schedules:
	./tests/verify-schedules.sh

verify-network-delay-measurement:
	@test -n "$(MEASUREMENT)" || { echo "MEASUREMENT=<path> is required" >&2; exit 2; }
	./tests/verify-network-delay-measurement.sh "$(MEASUREMENT)"
