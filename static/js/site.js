(function () {
  "use strict";

  // Header gets a stronger background/shadow once the page has scrolled.
  var header = document.querySelector("header.site");
  if (header) {
    var onScroll = function () {
      if (window.scrollY > 8) header.classList.add("is-scrolled");
      else header.classList.remove("is-scrolled");
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  // Marks the primary-nav link of the current section (aria-current) so it shows as active.
  (function () {
    var path = location.pathname.replace(/\/+$/, "") || "/";
    var best = null, bestLen = -1;
    document.querySelectorAll("nav.links > a[href]").forEach(function (a) {
      var h; try { h = new URL(a.href, location.origin).pathname.replace(/\/+$/, "") || "/"; } catch (e) { return; }
      var hit = h === "/" ? path === "/" : (path === h || path.indexOf(h + "/") === 0);
      if (hit && h.length > bestLen) { best = a; bestLen = h.length; }
    });
    if (best) best.setAttribute("aria-current", "page");
  })();


  // Menu mobile : panneau construit à partir des liens existants (une seule source de vérité).
  (function () {
    var btn = document.getElementById("mburger"), panel = document.getElementById("mnav"), back = document.getElementById("mnav-backdrop");
    var hdr = document.querySelector("header.site");
    if (!btn || !panel || !hdr) return;
    var mq = window.matchMedia("(max-width: 900px)");
    function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html) e.innerHTML = html; return e; }
    function linkFrom(a, cls) {
      var l = el("a", cls); l.href = a.getAttribute("href"); l.textContent = a.textContent.trim();
      if (a.getAttribute("aria-current")) l.setAttribute("aria-current", a.getAttribute("aria-current"));
      var t = a.getAttribute("title"); if (t) l.title = t;
      return l;
    }
    function build() {
      panel.innerHTML = "";
      var s = document.querySelector("form.hdr-search");
      if (s) {
        var f = el("form", "mn-search"); f.setAttribute("role", "search"); f.method = "GET"; f.action = s.getAttribute("action");
        var i = el("input"); i.type = "search"; i.name = "q"; i.placeholder = (s.querySelector("input").placeholder || "Rechercher…"); i.setAttribute("aria-label", s.querySelector("input").getAttribute("aria-label") || "Rechercher"); i.autocomplete = "off";
        var b = el("button", "", '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>'); b.type = "submit"; b.setAttribute("aria-label", "OK");
        f.appendChild(i); f.appendChild(b); panel.appendChild(f);
      }
      var nav = el("nav", "mn-links"); nav.setAttribute("aria-label", "Navigation");
      var src = document.querySelector("nav.links");
      if (src) Array.prototype.forEach.call(src.children, function (c) {
        if (c.tagName === "A") nav.appendChild(linkFrom(c, "mn-link"));
        else if (c.classList.contains("nav-dropdown")) {
          var d = el("details", "mn-group"), sm = el("summary", "", c.querySelector(":scope > span").textContent.trim());
          d.appendChild(sm);
          Array.prototype.forEach.call(c.querySelectorAll(".dropdown-panel a"), function (a) { d.appendChild(linkFrom(a, "mn-sub")); });
          nav.appendChild(d);
        }
      });
      panel.appendChild(nav);
      var acts = el("div", "mn-actions");
      Array.prototype.forEach.call(document.querySelectorAll(".header-actions > a"), function (a) {
        var c = a.classList.contains("lang-switch") ? "mn-lang" : (/contract-intelligence/.test(a.getAttribute("href")) ? "mn-cta" : "mn-act");
        acts.appendChild(linkFrom(a, c));
      });
      panel.appendChild(acts);
    }
    var lastFocus = null;
    function setOpen(open) {
      if (open && !panel.firstChild) build();
      if (open) document.documentElement.style.setProperty("--mn-top", Math.round(hdr.getBoundingClientRect().bottom) + "px");
      panel.hidden = back.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", btn.getAttribute(open ? "data-close" : "data-open"));
      document.documentElement.classList.toggle("mn-lock", open);
      hdr.classList.toggle("mn-open", open);
      if (open) { lastFocus = document.activeElement; panel.scrollTop = 0; panel.focus({ preventScroll: true }); }
      else if (lastFocus && document.contains(lastFocus)) { btn.focus({ preventScroll: true }); }
    }
    btn.addEventListener("click", function () { setOpen(panel.hidden); });
    back.addEventListener("click", function () { setOpen(false); });
    panel.addEventListener("click", function (e) { if (e.target.closest("a")) setOpen(false); });
    document.addEventListener("keydown", function (e) {
      if (panel.hidden) return;
      if (e.key === "Escape") { setOpen(false); return; }
      if (e.key === "Tab") {   // le focus reste dans le menu tant qu'il est ouvert
        var f = [btn].concat(Array.prototype.slice.call(panel.querySelectorAll("a[href],button,input,summary"))).filter(function (x) { return x.offsetParent !== null; });
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
    (mq.addEventListener ? mq.addEventListener.bind(mq, "change") : mq.addListener.bind(mq))(function () { if (!mq.matches && !panel.hidden) setOpen(false); });
  })();

  // Nav dropdowns (Plateforme/Administration/Aide-style menus): click/tap toggles
  // the panel open, so they work on touch devices where :hover never fires. Also
  // closes on an outside click/tap and on Escape, and only ever keeps one open.
  var navDropdowns = document.querySelectorAll(".nav-dropdown");
  if (navDropdowns.length) {
    var closeAllDropdowns = function (except) {
      navDropdowns.forEach(function (dd) {
        if (dd !== except) dd.classList.remove("open");
      });
    };
    navDropdowns.forEach(function (dd) {
      var trigger = dd.querySelector(":scope > span");
      if (!trigger) return;
      trigger.addEventListener("click", function (e) {
        e.stopPropagation();
        var willOpen = !dd.classList.contains("open");
        closeAllDropdowns(dd);
        dd.classList.toggle("open", willOpen);
      });
    });
    document.addEventListener("click", function () {
      closeAllDropdowns(null);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeAllDropdowns(null);
    });
  }

  // Scroll-reveal: elements marked .reveal / .reveal-stagger fade+rise into view once.
  var revealTargets = document.querySelectorAll(".reveal, .reveal-stagger");
  if ("IntersectionObserver" in window && revealTargets.length) {
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
    revealTargets.forEach(function (el) {
      io.observe(el);
    });
  } else {
    revealTargets.forEach(function (el) {
      el.classList.add("is-visible");
    });
  }

  // Stat count-up: elements with [data-count-to] animate from 0 once visible.
  var counters = document.querySelectorAll("[data-count-to]");
  if ("IntersectionObserver" in window && counters.length) {
    var animateCount = function (el) {
      var target = parseFloat(el.getAttribute("data-count-to"));
      var suffix = el.getAttribute("data-count-suffix") || "";
      var decimals = el.getAttribute("data-count-decimals") ? parseInt(el.getAttribute("data-count-decimals"), 10) : 0;
      if (isNaN(target)) return;
      var duration = 1100;
      var start = null;
      function step(ts) {
        if (start === null) start = ts;
        var progress = Math.min((ts - start) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        var value = target * eased;
        el.textContent = value.toFixed(decimals) + suffix;
        if (progress < 1) window.requestAnimationFrame(step);
        else el.textContent = target.toFixed(decimals) + suffix;
      }
      window.requestAnimationFrame(step);
    };
    var cio = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            animateCount(entry.target);
            cio.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.5 }
    );
    counters.forEach(function (el) {
      cio.observe(el);
    });
  }
})();
