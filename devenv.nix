{ pkgs, lib, config, inputs, ... }:

{
  nixpkgs.config.allowUnfree = true;

  languages.python = {
    enable = true;
    version = "3.12";
    uv = {
      enable = true;
    };
  };

  languages.go = {
    enable = true;
    version = "1.22";
  };

  packages = with pkgs; [
    git
    gnumake
    uv
    ruff
    pyright
    typos
    golangci-lint
    terraform
    kubectl
    kubernetes-helm
    k6
    grpcurl
    kustomize
  ];

  env = {
    KUBECONFIG = "$HOME/.kube/config-observability";
    OTEL_EXPORTER_OTLP_ENDPOINT = "http://otel-collector.observability.svc:4317";
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = "http://otel-collector.observability.svc:4317";
    OTEL_EXPORTER_OTLP_METRICS_ENDPOINT = "http://otel-collector.observability.svc:4317";
    OTEL_EXPORTER_OTLP_LOGS_ENDPOINT = "http://otel-collector.observability.svc:4317";
  };

  scripts = {
    up.exec = "make up";
    down.exec = "make down";
    clean.exec = "make clean";
    demo.exec = "make demo";
    lint.exec = "make lint";
    test.exec = "make test";
  };

  enterShell = ''
    echo "Observability Platform dev shell ready"
    echo "KUBECONFIG: $KUBECONFIG"
    echo "OTEL_EXPORTER_OTLP_ENDPOINT: $OTEL_EXPORTER_OTLP_ENDPOINT"
  '';
}
