---
name: proxy-network-fix
description: Use when network operations fail or when accessing known-blocked sites (GitHub, PyPI, npm, Go modules, package registries, etc.) from mainland China. Always enable proxy 192.168.5.100:7890 for these operations.
---

# Proxy Network Fix

China's Great Firewall (GFW) blocks or throttles many foreign websites and services. **Always enable proxy when accessing known-blocked targets**, not just when errors occur.

## Known-blocked targets (require proxy)

| Category | Examples |
|----------|----------|
| Code hosting | github.com, gitlab.com, bitbucket.org |
| Package registries | pypi.org (pip), npmjs.com (npm), proxy.golang.org (go), crates.io (rust), rubygems.org |
| Containers | docker.io, gcr.io, ghcr.io, quay.io |
| AI/ML | huggingface.co, openai.com, anthropic.com |
| Developer docs | readthedocs.io, docs.microsoft.com (partial), medium.com |
| Social/collab | twitter.com/x.com, discord.com, figma.com |
| OS/upstream | kernel.org, gnu.org, debian.org, ubuntu.com |

**For any of the above, enable proxy before attempting the operation.**

## How to enable proxy

### Git proxy (for git clone/push/pull)
```bash
git config http.proxy http://192.168.5.100:7890
git config https.proxy http://192.168.5.100:7890
```

### Environment-level proxy (for pip, curl, npm, go, etc.)
Always set when downloading packages or accessing blocked sites:
```bash
export http_proxy=http://192.168.5.100:7890
export https_proxy=http://192.168.5.100:7890
export HTTP_PROXY=http://192.168.5.100:7890
export HTTPS_PROXY=http://192.168.5.100:7890
```

For a single command without polluting the shell:
```bash
http_proxy=http://192.168.5.100:7890 https_proxy=http://192.168.5.100:7890 pip install <package>

http_proxy=http://192.168.5.100:7890 https_proxy=http://192.168.5.100:7890 npm install

http_proxy=http://192.168.5.100:7890 https_proxy=http://192.168.5.100:7890 git clone <url>
```

### Unset proxy (when accessing local/Chinese resources)
```bash
git config --unset http.proxy 2>/dev/null; git config --unset https.proxy 2>/dev/null
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
```

### SSH alternative for GitHub
```bash
# ~/.ssh/config
Host github.com
    HostName ssh.github.com
    Port 443
    User git
```

## Decision guide

| Situation | Proxy? |
|-----------|--------|
| Push/pull to GitHub/GitLab | **Yes** |
| `pip install`, `npm install`, `go get`, `cargo install` | **Yes** |
| Docker pull from docker.io | **Yes** |
| curl/wget to foreign site | **Yes** |
| Access to domestic services (gitee.com, aliyun.com, baidu.com) | **No** |
| Local network (NAS, router, LAN hosts) | **No** |
| GitHub 5xx, service outage | **No**, it's server-side |
| Authentication error (wrong token/credentials) | **No**, proxy won't help |

## Rule of thumb

If the operation involves a foreign domain that is known to be blocked or slow from mainland China, **enable proxy proactively** without waiting for a timeout. Timeouts waste minutes; a proxy flag costs nothing.
