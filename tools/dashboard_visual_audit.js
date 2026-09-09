#!/usr/bin/env node

/* Read-only visual contract for the DWARF dashboard.
 *
 * Usage:
 *   node tools/dashboard_visual_audit.js [base-url] [screenshot-directory]
 *
 * The crawler only performs top-level GET navigation. It never clicks controls,
 * submits forms, or visits API/tail endpoints.
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const baseUrl = (process.argv[2] || process.env.DWARF_DASHBOARD_URL || 'http://127.0.0.1:8787').replace(/\/$/, '');
const screenshotDir = process.argv[3] || '';

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
];

const ROUTES = [
  '/', '/tests', '/scenarios',
  '/operate', '/operate/audit', '/operate/bundles',
  '/operate/antithesis', '/operate/compare', '/operate/compare/runs',
  '/operate/config', '/operate/config/edit', '/operate/contract',
  '/operate/coverage', '/operate/crashes', '/operate/notifications', '/operate/plugins',
  '/operate/primitives/new', '/operate/profiles', '/operate/profiles/new', '/operate/runs',
  '/operate/scenarios', '/operate/scenarios/new', '/operate/schedule',
  '/operate/static-analysis', '/operate/status', '/operate/targets', '/operate/targets/new',
  '/operate/timeline',
  '/learn', '/learn/api', '/learn/architecture', '/learn/attack-cost', '/learn/cli',
  '/learn/concepts', '/learn/consensus', '/learn/coverage', '/learn/developer-onboarding',
  '/learn/examples', '/learn/faq', '/learn/getting-started', '/learn/glossary',
  '/learn/operator-runbook', '/learn/overview', '/learn/plugin-authoring', '/learn/status',
  '/learn/threat-coverage', '/learn/troubleshooting', '/learn/walkthroughs',
];

function safeName(route, viewport) {
  const slug = route.replace(/^\//, '').replace(/[^a-zA-Z0-9]+/g, '-') || 'root';
  return `${viewport}-${slug}.png`;
}

function sameOriginPath(href) {
  try {
    const url = new URL(href, baseUrl);
    if (url.origin !== new URL(baseUrl).origin) return '';
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/static/')) return '';
    if (url.pathname.endsWith('/tail') || url.pathname === '/metrics') return '';
    return url.pathname + url.search;
  } catch (_) {
    return '';
  }
}

async function representativeDynamicRoutes(page) {
  const representatives = [];
  for (const source of ['/operate/runs', '/operate/scenarios']) {
    try {
      await page.goto(baseUrl + source, { waitUntil: 'domcontentloaded', timeout: 15000 });
      const hrefs = await page.locator('a[href]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')));
      const paths = hrefs.map(sameOriginPath).filter(Boolean);
      if (source.endsWith('/runs')) {
        const run = paths.find(item => /^\/operate\/runs\/[^/?]+$/.test(item));
        if (run) representatives.push(run, `${run}/live`);
      } else {
        const edit = paths.find(item => item.startsWith('/operate/scenarios/edit/'));
        if (edit) representatives.push(edit);
      }
    } catch (_) {
      // The static source route will report the actionable failure later.
    }
  }
  return representatives;
}

async function inspectRoute(page, route, viewport) {
  const browserErrors = [];
  const onConsole = message => {
    if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
  };
  const onPageError = error => browserErrors.push(`page: ${error.message}`);
  page.on('console', onConsole);
  page.on('pageerror', onPageError);

  const problems = [];
  try {
    const response = await page.goto(baseUrl + route, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForLoadState('load', { timeout: 10000 });
    const status = response ? response.status() : 0;
    if (status >= 400 || status === 0) problems.push(`HTTP ${status || 'no response'}`);

    const metrics = await page.evaluate(async () => {
      const root = document.documentElement;
      const controls = [...document.querySelectorAll('input, select, textarea, button')]
        .filter(element => {
          const style = getComputedStyle(element);
          return style.display !== 'none' && style.visibility !== 'hidden' && element.type !== 'hidden';
        });
      const whiteControls = controls.filter(element => {
        const color = getComputedStyle(element).backgroundColor;
        return color === 'rgb(255, 255, 255)' || color === 'rgba(255, 255, 255, 1)';
      }).length;
      const brokenImages = [...document.images].filter(image => {
        const style = getComputedStyle(image);
        return style.display !== 'none' && (!image.complete || image.naturalWidth === 0);
      }).map(image => image.currentSrc || image.src);
      const unlabeledControls = [...document.querySelectorAll('input, select, textarea')]
        .filter(element => element.type !== 'hidden' && getComputedStyle(element).display !== 'none')
        .filter(element => {
          if (element.getAttribute('aria-label') || element.getAttribute('aria-labelledby')) return false;
          if (element.closest('label')) return false;
          return !element.id || !document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
        })
        .map(element => element.id || element.name || element.tagName.toLowerCase());
      const unnamedButtons = [...document.querySelectorAll('button')]
        .filter(element => getComputedStyle(element).display !== 'none')
        .filter(element => !(element.textContent || '').trim() && !element.getAttribute('aria-label'))
        .length;
      const missingHeadings = document.querySelectorAll('h1').length === 0 ? 1 : 0;
      const missingImageAlt = [...document.images].filter(image => !image.hasAttribute('alt')).length;
      const fragmentLinks = [...document.querySelectorAll('a[href*="#"]')]
        .map(anchor => anchor.href)
        .filter(href => {
          const target = new URL(href, location.href);
          return target.origin === location.origin && target.hash.length > 1;
        });
      const documents = new Map();
      const brokenFragments = [];
      for (const href of [...new Set(fragmentLinks)]) {
        const target = new URL(href, location.href);
        const documentKey = target.pathname + target.search;
        let targetDocument;
        if (documentKey === location.pathname + location.search) {
          targetDocument = document;
        } else if (documents.has(documentKey)) {
          targetDocument = documents.get(documentKey);
        } else {
          try {
            const response = await fetch(documentKey, { credentials: 'same-origin' });
            targetDocument = response.ok
              ? new DOMParser().parseFromString(await response.text(), 'text/html')
              : null;
          } catch (_) {
            targetDocument = null;
          }
          documents.set(documentKey, targetDocument);
        }
        let fragment;
        try {
          fragment = decodeURIComponent(target.hash.slice(1));
        } catch (_) {
          fragment = target.hash.slice(1);
        }
        if (!targetDocument || !targetDocument.getElementById(fragment)) {
          brokenFragments.push(target.pathname + target.hash);
        }
      }
      return {
        clientWidth: root.clientWidth,
        scrollWidth: root.scrollWidth,
        whiteControls,
        brokenImages,
        unlabeledControls,
        unnamedButtons,
        missingHeadings,
        missingImageAlt,
        brokenFragments,
      };
    });

    if (metrics.scrollWidth > metrics.clientWidth + 2) {
      problems.push(`document overflow ${metrics.scrollWidth}px > ${metrics.clientWidth}px`);
    }
    if (metrics.whiteControls) problems.push(`${metrics.whiteControls} browser-white controls`);
    if (metrics.brokenImages.length) problems.push(`broken images: ${metrics.brokenImages.join(', ')}`);
    if (metrics.unlabeledControls.length) problems.push(`unlabeled controls: ${metrics.unlabeledControls.join(', ')}`);
    if (metrics.unnamedButtons) problems.push(`${metrics.unnamedButtons} unnamed buttons`);
    if (metrics.missingHeadings) problems.push('missing h1');
    if (metrics.missingImageAlt) problems.push(`${metrics.missingImageAlt} images missing alt text`);
    if (metrics.brokenFragments.length) problems.push(`broken fragment links: ${metrics.brokenFragments.join(', ')}`);
    problems.push(...browserErrors);

    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true });
      await page.screenshot({ path: path.join(screenshotDir, safeName(route, viewport.name)), fullPage: true });
    }
  } catch (error) {
    problems.push(`navigation: ${error.message}`);
  } finally {
    page.off('console', onConsole);
    page.off('pageerror', onPageError);
  }
  return { route, viewport: viewport.name, problems };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const discoveryPage = await browser.newPage({ viewport: VIEWPORTS[0], reducedMotion: 'reduce' });
  const dynamicRoutes = await representativeDynamicRoutes(discoveryPage);
  await discoveryPage.close();
  const routes = [...new Set([...ROUTES, ...dynamicRoutes])];
  const results = [];

  for (const viewport of VIEWPORTS) {
    const page = await browser.newPage({ viewport, reducedMotion: 'reduce' });
    for (const route of routes) results.push(await inspectRoute(page, route, viewport));
    await page.close();
  }
  await browser.close();

  const failures = results.filter(result => result.problems.length);
  console.log(JSON.stringify({ baseUrl, routeCount: routes.length, checks: results.length, failures }, null, 2));
  if (failures.length) process.exitCode = 1;
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
