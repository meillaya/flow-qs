{ pkgs, lib, config, inputs, ... }:

{
  languages.python = {
    enable = true;
    version = "3.12";
    uv = {
      enable = true;
    };
  };

  languages.go = {
    enable = true;
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
    k3d
    kubernetes-helm
    k6
    grpcurl
    kustomize
    kubeconform
    yq-go
    jq
    curl
    prometheus
    prometheus.cli
    prometheus-alertmanager
    shellcheck
  ];

  env = {
    LD_LIBRARY_PATH = lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
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
    export GOTOOLCHAIN=go1.26.5
    export KUBECONFIG="$HOME/.kube/config-observability"
    echo "flow-qs dev shell ready"
    echo "KUBECONFIG: $KUBECONFIG"
    echo "OTEL_EXPORTER_OTLP_ENDPOINT: $OTEL_EXPORTER_OTLP_ENDPOINT"
  '';
}
