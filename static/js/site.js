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
