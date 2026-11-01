# Contributing

flow-qs runs on a local k3d cluster. You need Nix, Docker with at least 6 GiB
assigned, and internet access for the first chart and image pull.

## Set up

```bash
devenv shell
make test
```

The development shell provides Python 3.12, uv, Go, k3d, Terraform, kubectl,
Helm, k6, kubeconform, yq, and the lint tools. Run project commands inside it.

## Local gates

```bash
make lint       # Helm, Ruff, ty, gofmt, go vet, golangci-lint
make test       # pytest and go test
make contracts  # static contract tests, no cluster access
make rules      # SLO validation, rule regeneration, promtool
make prose      # house prose rules
make shellcheck # shell script checks
make check      # all of the above
```

`make contracts` installs Helm repositories and needs `helm`, `k6`,
`kubeconform`, and `yq`. The development shell supplies all four.

## Change rules

- Write the test first. Watch it fail, then make it pass.
- Keep one logical change per commit.
- Update the docs in the same change as the behavior.
- Regenerate architecture figures with `make figures` and inspect the PNGs.
  See [docs/DRAWINGS.md](docs/DRAWINGS.md).
- Do not commit credentials, kubeconfigs, or generated evidence under
  `.omo/`.

## Commit messages

Use the imperative mood in the subject, capitalize the first word, keep the
subject near 50 characters, and leave off the final period. Separate the
subject from the body with one blank line. Wrap the body at 72 characters and
explain why the change exists.

## License

Contributions are accepted under the MIT license in [LICENSE](LICENSE).
