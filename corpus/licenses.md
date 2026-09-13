# Corpus license references

The v1 manifest contains metadata only; it does not clone or redistribute
third-party source. Before preparing repository-specific cases, re-check the
license at the exact commit and preserve the upstream URL and commit in the
case record. The current references are the MIT license files for [ripgrep](https://github.com/BurntSushi/ripgrep/blob/3fce3b5bb0236da2df6d99672afb8a719642eca7/LICENSE-MIT), [bat](https://github.com/sharkdp/bat/blob/7323a7514f7601737640e7172be115127d6db08c/LICENSE-MIT), and [serde](https://github.com/serde-rs/serde/blob/a874a1b1bb1cc16cf5ee3b1b7b527af5705742bb/LICENSE-MIT).

`git ls-remote` verified each pinned branch ref on 2026-09-13. A future run
must fail or update the manifest review if the remote ref no longer resolves to
the recorded commit.
