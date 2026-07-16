# Runbook

Operational playbooks for the flow-qs observability platform.

Commands below that use `127.0.0.1:3000`, `:9090`, or `:9093` assume the
matching `kubectl port-forward` from [SETUP.md](./SETUP.md) is running. The
platform Services are not permanently exposed on the host.

## Common Operations

### A service is down

Symptoms: `ServiceDown` alert fires; Grafana shows target missing; `/healthz` does not respond.

1. Check pod status:

   ```bash
   kubectl get pods -n ecom -l app.kubernetes.io/name=<service>
   ```

2. Read recent events:

   ```bash
   kubectl describe pod -n ecom <pod-name>
   ```

3. Read logs:

   ```bash
   kubectl logs -n ecom <pod-name> --tail=200
   ```

4. If the pod is CrashLooping, check resource limits and image pull status:

   ```bash
   kubectl get events -n ecom --sort-by='.lastTimestamp'
   ```

5. Restart the deployment:

   ```bash
   kubectl rollout restart deployment/<service> -n ecom
   ```

6. Verify recovery:

   ```bash
   kubectl rollout status deployment/<service> -n ecom
   kubectl -n ecom port-forward svc/<service> <port>:<port>
   curl -fsS http://127.0.0.1:<port>/healthz
   ```

### An alert is firing

Symptoms: Alertmanager UI shows an alert; PagerDuty/Slack notification; Grafana panel red.

1. Identify the alert:

   ```bash
   curl -fsS http://127.0.0.1:9093/api/v2/alerts | jq '.[].labels.alertname'
   ```

2. Find the affected service and SLO kind from the alert labels.
3. Query Prometheus for the underlying SLI:

   ```bash
   curl -fsS -G http://127.0.0.1:9090/api/v1/query \
     --data-urlencode 'query={__name__="sli:<service>:<slo_kind>:ratio_1h"}' | jq
   ```

4. Look at the RED dashboard in Grafana for that service.
5. Check recent traces in Grafana Trace Explorer filtered by `service.name`.
6. If the alert is from a chaos experiment, decide whether to let it run or delete it:

   ```bash
   kubectl delete -f chaos/experiments/<experiment>.yaml -n chaos-testing
   ```

7. After recovery, verify the alert resolves in Alertmanager.

### Postmortem was not generated

Symptoms: Alert fired but no postmortem appears on the incident-engine PVC at `/postmortems`. The host `docs/examples/postmortems/` directory changes only when `make demo` copies its correlated result out of the pod.

1. Check the incident engine pod:

   ```bash
   kubectl get pods -n incident
   kubectl logs -n incident deployment/incident-engine --tail=200
   ```

2. Verify Alertmanager can reach the webhook:

   ```bash
   kubectl run -n incident debug --rm -i --restart=Never --image=curlimages/curl \
     -- http://incident-engine.incident.svc:8088/webhook -X POST -d '{}'
   ```

3. Check Alertmanager status:

   ```bash
   curl -fsS http://127.0.0.1:9093/api/v2/status | jq
   ```

4. Confirm the route points to `incident-engine`:

   ```bash
   curl -fsS http://127.0.0.1:9093/api/v2/status | jq -r '.config.original'
   ```

5. Restart the incident engine if the webhook handler panicked:

   ```bash
   kubectl rollout restart deployment/incident-engine -n incident
   ```

### Grafana is broken

Symptoms: Grafana returns 502; dashboards missing; login fails.

1. Check pod status:

   ```bash
   kubectl get pods -n observability -l app.kubernetes.io/name=grafana
   ```

2. Read logs:

   ```bash
   kubectl logs -n observability <grafana-pod>
   ```

3. Verify datasources are provisioned:

   ```bash
   kubectl get configmap -n observability grafana-datasources
   ```

4. Restart Grafana:

   ```bash
   kubectl rollout restart deployment/grafana -n observability
   ```

5. If dashboards are missing, confirm the dashboard sidecar has loaded the JSON files:

   ```bash
   kubectl logs -n observability <grafana-pod> -c dashboard-sidecar
   ```

### Prometheus is full

Symptoms: Prometheus pod is in `Evicted` or `CrashLoopBackOff`; disk pressure warnings; queries time out.

1. Check PVC usage:

   ```bash
   kubectl get pvc -n observability
   kubectl describe pvc -n observability prometheus-server
   ```

2. Check disk pressure on nodes:

   ```bash
   kubectl describe node | grep -A 5 "Conditions"
   ```

3. Do not reduce retention below 31 days: every SLO has a 30-day ratio and
   budget dependency. Increase storage or reduce metric cardinality instead.

4. For a disposable local reset, tear down and recreate the project. This
   permanently deletes local metrics, logs, and traces.

   ```bash
   make down
   make up
   ```

5. flow-qs does not claim object-storage backup or in-place PVC migration.

## Cookbooks

### How to add a new service

1. Create a service directory under `services/<new-service>/`.
2. Add the application code, Dockerfile, `pyproject.toml` or `go.mod`, and tests.
3. Export traces and the canonical
   `http_server_request_duration_seconds_{bucket,count,sum}` series through
   OTLP. Use `service_name`, `service_namespace`,
   `http_response_status_code`, and low-cardinality `http_route` labels.
   Write application logs to stdout/stderr for the node log agents.
4. Create a Helm chart under `charts/<new-service>/`.
5. Do not add a second scrape path. Payment's direct scrape is the bounded
   exception for its existing no-new-dependency constraint.
6. Add an SLO file under `slo/<new-service>.yaml`.
7. Rebuild Prometheus rules:

   ```bash
   uv run --with pyyaml python scripts/build_prometheus_rules.py
   uv run scripts/validate_observability_contract.py
   kubectl apply -k observability/prometheus-rules
   ```

8. Deploy the service:

   ```bash
   helm upgrade --install <new-service> charts/<new-service> -n ecom
   ```

9. Update the architecture diagram in `docs/ARCHITECTURE.md` and the stack table in `README.md`.

### How to add a new SLO

1. Copy an existing SLO file, for example `slo/catalog-service.yaml`, to `slo/<service>.yaml`.
2. Set the service name, objective, window, and selectors.
3. Compute the error budget:

   ```python
   budget_sec = (1 - objective) * 30 * 86400
   print(f"{int(budget_sec // 60)}m{int(budget_sec % 60)}s")
   ```

4. Validate the file:

   ```bash
   uv run --with pyyaml python scripts/validate_slo.py slo/<service>.yaml
   ```

5. Regenerate Prometheus rules:

   ```bash
   uv run --with pyyaml python scripts/build_prometheus_rules.py
   ```

6. Apply the new rules:

   ```bash
   kubectl apply -k observability/prometheus-rules
   ```

7. With the Prometheus forward running, verify the new recording rules at
   `http://127.0.0.1:9090/rules` and query the exact service-qualified record.

### How to add a new chaos experiment

1. Choose a fault type:

   - `PodChaos` for pod/container failures
   - `NetworkChaos` for latency, loss, or partition
   - `DNSChaos` for DNS failures
   - `StressChaos` for CPU or memory pressure

2. Create a YAML file under `chaos/experiments/<name>.yaml`.
3. Target the correct namespace and labels. Example:

   ```yaml
   apiVersion: chaos-mesh.org/v1alpha1
   kind: NetworkChaos
   metadata:
     name: network-delay-order
     namespace: chaos-testing
   spec:
     action: delay
     mode: one
     selector:
       namespaces:
         - ecom
       labelSelectors:
         app.kubernetes.io/name: order-service
     delay:
       latency: 500ms
       correlation: "100"
       jitter: 0ms
   ```

4. Dry-run the experiment:

   ```bash
   kubectl apply --dry-run=server -f chaos/experiments/<name>.yaml
   ```

5. Apply it:

   ```bash
   kubectl apply -f chaos/experiments/<name>.yaml
   ```

6. Watch for the alert in Prometheus and the postmortem from the incident engine.
7. Clean up:

   ```bash
   kubectl delete -f chaos/experiments/<name>.yaml
   ```

8. Add the experiment to `chaos/Makefile` and document it in `docs/DEMO.md`.

## Related Docs

- [ARCHITECTURE.md](./ARCHITECTURE.md): system design and telemetry flows
- [DEMO.md](./DEMO.md): running the demo
- [SETUP.md](./SETUP.md): installation and provisioning
