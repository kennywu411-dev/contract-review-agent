"""下载 data/sources.toml 里的文件到 data/raw/，并更新 sources.lock.json。"""

from contract_review.sources import fetch_all, load_sources, write_lock


def main() -> None:
    report = fetch_all(load_sources())
    write_lock(report)
    for name in ("downloaded", "skipped", "missing_manual", "failed"):
        ids = getattr(report, name)
        print(f"{name:15} {len(ids):3}  {' '.join(ids)}")


if __name__ == "__main__":
    main()
