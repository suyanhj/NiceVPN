import { defineComponent as g, ref as v, computed as r, openBlock as i, createElementBlock as s, Fragment as w, renderList as p, normalizeClass as k } from "vue";
/* empty css                */
const y = ["width", "height"], _ = ["x", "y", "width", "height", "onMouseenter"], $ = /* @__PURE__ */ g({
  __name: "VpnInteractiveGrid",
  props: {
    width: { default: 36 },
    height: { default: 36 },
    columns: { default: 24 },
    rows: { default: 18 }
  },
  setup(n) {
    const t = n, l = v(null), a = r(() => t.columns * t.rows), h = r(() => t.width * t.columns), c = r(() => t.height * t.rows);
    function d(o) {
      return o % t.columns * t.width;
    }
    function f(o) {
      return Math.floor(o / t.columns) * t.height;
    }
    return (o, u) => (i(), s("svg", {
      width: h.value,
      height: c.value,
      class: "pointer-events-auto absolute inset-0 h-full w-full opacity-45",
      "aria-hidden": "true"
    }, [
      (i(!0), s(w, null, p(a.value, (M, e) => (i(), s("rect", {
        key: e,
        x: d(e),
        y: f(e),
        width: n.width,
        height: n.height,
        class: k(["fill-transparent stroke-white/[0.035] transition-colors duration-700", { "!fill-[#6ea8ff]/[0.055] !stroke-[#6ea8ff]/10 !duration-100": l.value === e }]),
        onMouseenter: (m) => l.value = e,
        onMouseleave: u[0] || (u[0] = (m) => l.value = null)
      }, null, 42, _))), 128))
    ], 8, y));
  }
});
export {
  $ as default
};
