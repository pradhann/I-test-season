import { el } from "/js/app.js";
export default async function view(host) {
  host.appendChild(el("div", "empty", "This view is being built."));
}
