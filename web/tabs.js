// tabs.js — small generic tab-switcher, used by admin.html (Sensors |
// Tuning | Override | Status). Finds .tab-button/.tab-content pairs by
// matching data-tab, wires clicks to toggle an "active" class.

function initTabs(container) {
  const buttons = container.querySelectorAll(".tab-button");
  const panes = container.querySelectorAll(".tab-content");

  function activate(name) {
    for (const button of buttons) {
      button.classList.toggle("active", button.dataset.tab === name);
    }
    for (const pane of panes) {
      pane.classList.toggle("active", pane.id === `tab-${name}`);
    }
  }

  for (const button of buttons) {
    button.addEventListener("click", () => activate(button.dataset.tab));
  }

  if (buttons.length > 0) {
    activate(buttons[0].dataset.tab);
  }
}

window.initTabs = initTabs;
