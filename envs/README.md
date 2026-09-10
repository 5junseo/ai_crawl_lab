# envs/

**1 도구 = 1 env.** 도구끼리 playwright / litellm / pydantic 버전이 충돌하므로 한 env 에 몰면 안 된다.
harness 자체(`aicrawl`)는 도구를 하나도 import 하지 않는다 — 어댑터를 서브프로세스/별도 env 에서 돌리기 위함.

env 이름은 `adapters/registry.py` 의 `ToolSpec.env` 와 **같아야 한다**. harness 가 그 이름으로
어느 env 에서 돌릴지 안내한다.

```bash
conda create -n aicrawl         python=3.11 -y && conda activate aicrawl         && pip install -r envs/aicrawl.txt
conda create -n scrapy_baseline python=3.11 -y && conda activate scrapy_baseline && pip install -r envs/scrapy_baseline.txt
conda create -n autoscraper     python=3.10 -y && conda activate autoscraper     && pip install -r envs/autoscraper.txt
conda create -n crawl4ai        python=3.11 -y && conda activate crawl4ai        && pip install -r envs/crawl4ai.txt    && crawl4ai-setup
conda create -n scrapegraphai   python=3.11 -y && conda activate scrapegraphai   && pip install -r envs/scrapegraphai.txt && playwright install chromium
conda create -n firecrawl       python=3.11 -y && conda activate firecrawl       && pip install -r envs/firecrawl.txt
conda create -n browseruse      python=3.12 -y && conda activate browseruse      && pip install -r envs/browseruse.txt
# Skyvern 은 Docker 로 격리한다 (envs/skyvern.md 참고)
```

`browseruse` 는 **playwright 를 안 쓴다.** 0.13 대부터 CDP 로 브라우저에 직접 붙으므로
(`cdp-use`) `playwright install` 이 필요 없고, 브라우저 바이너리는 시스템 Chrome 을 도구가
찾아 쓴다.

`scrapegraphai.txt` 는 버전을 전부 못 박아 뒀다. 최신판이 **설치는 되는데 import 가 안 되기**
때문이다 — 이유는 그 파일 주석에 적어 뒀다.

