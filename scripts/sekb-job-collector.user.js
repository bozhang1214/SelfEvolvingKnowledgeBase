// ==UserScript==
// @name         SEKB 职位采集（BOSS直聘 / 智联招聘）
// @namespace    https://github.com/bozhang1214/SelfEvolvingKnowledgeBase
// @version      1.0.0
// @description  在 BOSS 直聘 / 智联招聘 的职位页一键采集「当前页已渲染」的职位，导出成 SEKB 可直接导入的 JSON。半自动、人工触发、只读 DOM、不发任何网络请求 —— 不批量、不加速、不绕风控。
// @author       SEKB
// @match        https://www.zhipin.com/web/geek/jobs*
// @match        https://www.zhaopin.com/recommend*
// @match        https://www.zhaopin.com/sou/*
// @match        https://sou.zhaopin.com/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

/**
 * 为什么只读 DOM、不发请求（这是设计选择，不是偷懒）
 * ---------------------------------------------------------------
 * BOSS 的搜索接口有 `sign`/`__zp_stoken__` 风控（社区实测返回 code:37），
 * 同源 fetch 详情页也会触发 `_security_check`（见 rockbenben/ai-job-search-cn
 * 的 `workflows/reference/cdp-portals.md` 实测记录）。
 * 而「读页面已渲染的 DOM」不发出任何新请求 —— 那是人打开页面后本来就发生的事，
 * 在风控视角下与「人看了一眼」等价。
 *
 * 所以本脚本：
 *   ✅ 只读当前页 DOM（点击「采集当前页」才执行一次）
 *   ✅ 采集结果存本机 localStorage，可跨页累积、可导出 JSON
 *   ❌ 不自动翻页、不自动点击、不定时轮询
 *   ❌ 不发任何网络请求（没有 fetch / XMLHttpRequest / GM_xmlhttpRequest）
 *
 * 使用节奏建议：一页看完 → 点一次采集 → 手动翻页 → 再点一次。
 * 撞到验证码/风控提示就停下（别硬闯）。
 */

(function () {
  'use strict';

  const STORE_KEY = 'sekb_job_collector_v1';

  // ─────────────────────────── 站点适配器 ───────────────────────────
  // 选择器取自实测文档（rockbenben/ai-job-search-cn · cdp-portals.md，2026-08），
  // 每项都给多个候选：站点改版时先失效的是精确类名，退化选择器能兜住一部分。
  const ADAPTERS = [
    {
      name: 'BOSS直聘',
      test: () => /zhipin\.com\/web\/geek\/jobs/.test(location.href),
      // 实测卡片是 .job-card-wrap（不是 .job-card-wrapper），两个都试
      cards: () => document.querySelectorAll('.job-card-wrap, .job-card-wrapper'),
      parse: (card) => ({
        title: pickText(card, ['.job-name', '.job-title', '[class*="job-name"]']),
        company: pickText(card, ['.company-name', '[class*="company-name"]']),
        salary: pickText(card, ['.salary', '[class*="salary"]']),
        city: pickText(card, ['.job-area', '[class*="job-area"]']),
        job_url: pickHref(card, ['a[href*="/job_detail/"]', 'a[href*="job_detail"]']),
        tags: pickTexts(card, ['.tag-item', '.job-card-footer .tag-item', '[class*="tag-item"]']),
      }),
    },
    {
      name: '智联招聘',
      test: () => /zhaopin\.com\/(recommend|sou)/.test(location.href) || /sou\.zhaopin\.com/.test(location.href),
      cards: () => document.querySelectorAll('.joblist-box__item.clearfix, .joblist-box__item'),
      parse: (card) => ({
        title: pickText(card, ['.jname', '[class*="jname"]']),
        company: pickText(card, ['.cname', '[class*="cname"]']),
        salary: pickText(card, ['.sal', '[class*="sal"]']),
        // .shrink-0 是「城市·区」，实测 20/20 命中
        city: pickText(card, ['.shrink-0', '[class*="shrink-0"]']),
        industry: pickText(card, ['.dc']),
        job_url: pickHref(card, ['a[href*="jobdetail"]', 'a[href*="job_detail"]']),
        tags: pickTexts(card, ['.tag']),
      }),
    },
  ];

  // ─────────────────────────── 小工具 ───────────────────────────

  function pickText(root, selectors) {
    for (const sel of selectors) {
      const el = root.querySelector(sel);
      if (el) {
        const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
        if (t) return t;
      }
    }
    return '';
  }

  function pickTexts(root, selectors) {
    for (const sel of selectors) {
      const els = root.querySelectorAll(sel);
      if (els.length) {
        return Array.from(els)
          .map((e) => (e.textContent || '').replace(/\s+/g, ' ').trim())
          .filter(Boolean);
      }
    }
    return [];
  }

  function pickHref(root, selectors) {
    for (const sel of selectors) {
      const a = root.querySelector(sel);
      // 用 a.href（浏览器已解析成绝对地址）；getAttribute('href') 给的是相对路径
      if (a && a.href) return a.href.split('#')[0];
    }
    return '';
  }

  /** 从 URL 里抠出职位 id，用于去重与 job_id（各站格式不同，抠不到就返回 ''）。 */
  function jobIdFromUrl(url) {
    if (!url) return '';
    let m = url.match(/job_detail\/([^./?]+)/);
    if (m) return m[1];
    m = url.match(/jobdetail\/([^./?]+)/);
    if (m) return m[1];
    return '';
  }

  function dedupeKey(job) {
    return job.job_url || `${job.title}|${job.company}`;
  }

  function currentAdapter() {
    return ADAPTERS.find((a) => a.test()) || null;
  }

  // ─────────────────────────── 采集与存储 ───────────────────────────

  function loadStore() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      if (parsed && Array.isArray(parsed.jobs)) return parsed;
    } catch (e) {
      /* 坏数据当作空 */
    }
    return { jobs: [], updatedAt: '' };
  }

  function saveStore(store) {
    store.updatedAt = new Date().toISOString();
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(store));
    } catch (e) {
      /* 配额满等，忽略 */
    }
  }

  /** 采集当前页：只读 DOM，不发请求。返回 {added, total, found}。 */
  function collectCurrentPage() {
    const adapter = currentAdapter();
    if (!adapter) return { added: 0, total: 0, found: 0, error: '当前页面不是受支持的职位页' };

    const cards = adapter.cards();
    const found = cards.length;
    const store = loadStore();
    const seen = new Set(store.jobs.map(dedupeKey));
    let added = 0;

    cards.forEach((card) => {
      const raw = adapter.parse(card);
      if (!raw.title && !raw.job_url) return; // 空卡片跳过
      const job = {
        job_id: jobIdFromUrl(raw.job_url),
        title: raw.title,
        company: raw.company,
        salary: raw.salary,
        city: raw.city,
        source: adapter.name,
        job_url: raw.job_url,
        jd_text: '', // 列表页拿不到 JD 全文；SEKB 侧可用「刷新 JD」或走单职位分析补
        _tags: raw.tags || [],
        _industry: raw.industry || '',
        _collectedAt: new Date().toISOString(),
      };
      const key = dedupeKey(job);
      if (seen.has(key)) return;
      seen.add(key);
      store.jobs.push(job);
      added += 1;
    });

    saveStore(store);
    return { added, total: store.jobs.length, found, error: '' };
  }

  /** 导出成 SEKB 可导入的 JSON（结构对齐 backend/app/services/job_service.py 的解析）。 */
  function exportPayload() {
    const store = loadStore();
    return {
      source: 'SEKB 职位采集插件',
      exportedAt: new Date().toISOString(),
      count: store.jobs.length,
      jobs: store.jobs.map((j) => ({
        job_id: j.job_id || '',
        title: j.title || '',
        company: j.company || '',
        salary: j.salary || '',
        city: j.city || '',
        source: j.source || '',
        job_url: j.job_url || '',
        jd_text: j.jd_text || '',
      })),
    };
  }

  function downloadJson() {
    const payload = exportPayload();
    if (!payload.count) {
      toast('还没有采集到职位');
      return;
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json;charset=utf-8',
    });
    const a = document.createElement('a');
    const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '');
    a.href = URL.createObjectURL(blob);
    a.download = `sekb-jobs-${ts}.json`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(a.href);
      a.remove();
    }, 0);
    toast(`已导出 ${payload.count} 条`);
  }

  async function copyJson() {
    const payload = exportPayload();
    if (!payload.count) {
      toast('还没有采集到职位');
      return;
    }
    const text = JSON.stringify(payload, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      toast(`已复制 ${payload.count} 条到剪贴板`);
    } catch (e) {
      // 剪贴板 API 在非安全上下文/无权限时会失败，退回到临时 textarea
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        toast(`已复制 ${payload.count} 条`);
      } catch (e2) {
        toast('复制失败，请用「导出 JSON」');
      } finally {
        ta.remove();
      }
    }
  }

  // ─────────────────────────── 面板 UI ───────────────────────────

  let panel = null;
  let toastTimer = null;

  function toast(msg) {
    if (!panel) return;
    let el = panel.querySelector('.sekb-toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'sekb-toast';
      panel.appendChild(el);
    }
    el.textContent = msg;
    el.style.opacity = '1';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.style.opacity = '0';
    }, 2600);
  }

  function refreshPanel() {
    if (!panel) return;
    const adapter = currentAdapter();
    const store = loadStore();
    const site = adapter ? adapter.name : '（当前页不支持）';
    const cards = adapter ? adapter.cards().length : 0;

    panel.querySelector('.sekb-site').textContent = `站点：${site}`;
    panel.querySelector('.sekb-stat').textContent =
      `当前页可采 ${cards} 条 · 已累计 ${store.jobs.length} 条`;
    panel.querySelector('.sekb-collect').disabled = !adapter || cards === 0;

    // 最近 5 条预览
    const list = panel.querySelector('.sekb-list');
    list.innerHTML = '';
    store.jobs
      .slice(-5)
      .reverse()
      .forEach((j) => {
        const li = document.createElement('div');
        li.className = 'sekb-item';
        li.textContent = `${j.title || '(无标题)'} — ${j.company || '?'} · ${j.salary || '面议'}`;
        list.appendChild(li);
      });
    if (!store.jobs.length) {
      const li = document.createElement('div');
      li.className = 'sekb-item sekb-empty';
      li.textContent = '还没有采集记录';
      list.appendChild(li);
    }
  }

  function buildPanel() {
    if (panel) return;
    if (!currentAdapter()) return;

    panel = document.createElement('div');
    panel.id = 'sekb-job-collector-panel';
    panel.innerHTML = `
      <div class="sekb-head">
        <span class="sekb-title">SEKB 职位采集</span>
        <span class="sekb-toggle" title="收起/展开">–</span>
      </div>
      <div class="sekb-body">
        <div class="sekb-site"></div>
        <div class="sekb-stat"></div>
        <div class="sekb-btns">
          <button class="sekb-btn sekb-collect">采集当前页</button>
          <button class="sekb-btn sekb-download">导出 JSON</button>
          <button class="sekb-btn sekb-copy">复制 JSON</button>
          <button class="sekb-btn sekb-clear" title="清空本机已采集记录">清空</button>
        </div>
        <div class="sekb-list"></div>
        <div class="sekb-hint">只读当前页 DOM，不发请求。撞到验证码请停手。</div>
      </div>
      <div class="sekb-toast"></div>
    `;
    document.body.appendChild(panel);

    const style = document.createElement('style');
    style.textContent = `
      #sekb-job-collector-panel {
        position: fixed; right: 18px; bottom: 18px; z-index: 2147483646;
        width: 268px; background: #fff; color: #222;
        border: 1px solid #d0d7de; border-radius: 10px;
        box-shadow: 0 6px 24px rgba(0,0,0,.16);
        font: 12px/1.5 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
        overflow: hidden;
      }
      #sekb-job-collector-panel .sekb-head {
        display: flex; align-items: center; justify-content: space-between;
        padding: 8px 10px; background: #1f6feb; color: #fff; cursor: move;
        font-weight: 600;
      }
      #sekb-job-collector-panel .sekb-toggle { cursor: pointer; padding: 0 4px; }
      #sekb-job-collector-panel .sekb-body { padding: 10px; }
      #sekb-job-collector-panel.sekb-collapsed .sekb-body { display: none; }
      #sekb-job-collector-panel .sekb-site { color: #57606a; }
      #sekb-job-collector-panel .sekb-stat { margin-bottom: 8px; color: #1f6feb; font-weight: 600; }
      #sekb-job-collector-panel .sekb-btns { display: flex; flex-wrap: wrap; gap: 6px; }
      #sekb-job-collector-panel .sekb-btn {
        flex: 1 1 46%; padding: 5px 6px; cursor: pointer;
        border: 1px solid #d0d7de; border-radius: 6px;
        background: #f6f8fa; color: #24292f; font-size: 12px;
      }
      #sekb-job-collector-panel .sekb-btn:hover:not(:disabled) { background: #eaeef2; }
      #sekb-job-collector-panel .sekb-btn:disabled { opacity: .5; cursor: not-allowed; }
      #sekb-job-collector-panel .sekb-collect { background: #1f6feb; color: #fff; border-color: #1f6feb; }
      #sekb-job-collector-panel .sekb-list { margin-top: 8px; max-height: 120px; overflow: auto; }
      #sekb-job-collector-panel .sekb-item {
        padding: 3px 0; border-top: 1px dashed #eaeef2;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
      }
      #sekb-job-collector-panel .sekb-empty { color: #8c959f; }
      #sekb-job-collector-panel .sekb-hint { margin-top: 8px; color: #8c959f; font-size: 11px; }
      #sekb-job-collector-panel .sekb-toast {
        position: absolute; left: 0; right: 0; bottom: 0; padding: 6px 10px;
        background: #1f6feb; color: #fff; opacity: 0; transition: opacity .25s;
        pointer-events: none;
      }
    `;
    document.head.appendChild(style);

    // 事件
    panel.querySelector('.sekb-collect').addEventListener('click', () => {
      const r = collectCurrentPage();
      if (r.error) toast(r.error);
      else toast(`本页 ${r.found} 条，新增 ${r.added} 条（累计 ${r.total}）`);
      refreshPanel();
    });
    panel.querySelector('.sekb-download').addEventListener('click', downloadJson);
    panel.querySelector('.sekb-copy').addEventListener('click', copyJson);
    panel.querySelector('.sekb-clear').addEventListener('click', () => {
      if (!confirm('清空本机已采集的职位记录？（不影响线上数据）')) return;
      saveStore({ jobs: [] });
      refreshPanel();
      toast('已清空');
    });
    panel.querySelector('.sekb-toggle').addEventListener('click', () => {
      panel.classList.toggle('sekb-collapsed');
      const t = panel.querySelector('.sekb-toggle');
      t.textContent = panel.classList.contains('sekb-collapsed') ? '+' : '–';
    });

    makeDraggable(panel, panel.querySelector('.sekb-head'));
    refreshPanel();
  }

  function makeDraggable(el, handle) {
    let sx = 0, sy = 0, ox = 0, oy = 0, dragging = false;
    handle.addEventListener('mousedown', (e) => {
      dragging = true;
      sx = e.clientX; sy = e.clientY;
      const rect = el.getBoundingClientRect();
      ox = rect.left; oy = rect.top;
      el.style.right = 'auto'; el.style.bottom = 'auto';
      el.style.left = `${ox}px`; el.style.top = `${oy}px`;
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      el.style.left = `${ox + (e.clientX - sx)}px`;
      el.style.top = `${oy + (e.clientY - sy)}px`;
    });
    document.addEventListener('mouseup', () => { dragging = false; });
  }

  // ─────────────────────────── 启动 ───────────────────────────
  // 结果页可能是 SPA 路由切换（BOSS/智联都会），所以用定时器轻量轮询 DOM 是否就绪，
  // **只查 DOM、不发请求**；面板只在受支持页面出现。
  let lastHref = location.href;
  function boot() {
    if (currentAdapter()) {
      buildPanel();
      if (panel) refreshPanel();
    } else if (panel) {
      panel.remove();
      panel = null;
    }
  }

  boot();
  setInterval(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      boot();
    }
  }, 1500);
})();
