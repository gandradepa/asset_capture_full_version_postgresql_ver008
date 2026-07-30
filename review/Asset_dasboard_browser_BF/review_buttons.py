# review_buttons.py -- canonical registry of the per-asset review page action buttons.
#
# THREE-COPY RULE: this file exists byte-identically in the ME, BF, and EL review
# apps (same rule as review_asset_templates/static/image-viewer.js). Edit all
# three copies together. Per-app differences (Flask endpoint names, EL's dynamic
# base_route dashboard target) are NOT stored here -- each app passes a
# `review_endpoints` dict to the template alongside REVIEW_BUTTONS.
#
# Rendered by review_asset_templates/macros/review_buttons.html.
# The dom_id values are a contract with the inline <script> in review.html
# (save-stay / approve-toggle wiring and lock handling) -- never change them.

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ReviewButton:
    key: str                  # save_stay|approve_toggle|pdf|export|dashboard|prev|save|save_next|skip
    label: str                # visible text (approve_toggle renders Pending/Approved dynamically)
    position: str             # "top" | "footer"
    order: int                # left-to-right order within position (10, 20, 30, ...)
    kind: str                 # "submit" (name=action button) | "js" (type=button wired by inline JS) | "link" (<a>)
    action: Optional[str] = None        # form action value; for kind="js" it is the value the
                                        # inline JS injects (informational -- keep in sync with review.html <script>)
    endpoint_key: Optional[str] = None  # kind="link": key into the per-app review_endpoints dict
    dom_id: Optional[str] = None        # exact element id -- JS contract, never change
    css_class: str = ""                 # exact class attr (dynamic pending/approved added by macro)
    icon: Optional[str] = None          # bootstrap-icons class
    title: Optional[str] = None         # static title attr (package-lock title handled by macro)
    lock: Optional[str] = None          # None | "package" (disabled + aria-disabled when package_locked)
                                        #      | "data-lock-save" (attr data-lock-save="true"; JS disables when review_locked)
    locked_label: Optional[str] = None  # save_next only: label shown when review_locked
    separator_before: bool = False      # render <div class="vr mx-1"></div> before this button
    target_blank: bool = False          # links: target="_blank" rel="noopener"
    embedded_query: bool = False        # links: append ?embedded=true when embedded


REVIEW_BUTTONS = [
    # ---------- top bar (canonical order: Save, Pending, PDF, Export, Dashboard) ----------
    ReviewButton(key="save_stay", label="Save", position="top", order=10, kind="js",
                 action="save_stay", dom_id="saveStayNav", css_class="save-stay-btn",
                 icon="bi bi-floppy2-fill", title="Save changes and stay on this page",
                 lock="package"),
    ReviewButton(key="approve_toggle", label="Pending", position="top", order=20, kind="js",
                 action="save_toggle", dom_id="approveToggleNav", css_class="approve-toggle-btn",
                 lock="package"),
    ReviewButton(key="pdf", label="PDF", position="top", order=30, kind="link",
                 endpoint_key="print", dom_id="printPdfTop",
                 css_class="btn btn-sm btn-outline-danger", icon="bi bi-file-earmark-pdf",
                 title="Open a printable view and Save as PDF", target_blank=True),
    ReviewButton(key="export", label="Export", position="top", order=40, kind="link",
                 endpoint_key="export", dom_id="exportHtmlTop",
                 css_class="btn btn-sm btn-outline-secondary", icon="bi bi-download",
                 title="Export this asset as a self-contained HTML file (download)"),
    ReviewButton(key="dashboard", label="Dashboard", position="top", order=50, kind="link",
                 endpoint_key="dashboard", dom_id="backTop",
                 css_class="btn btn-sm btn-outline-secondary", icon="bi bi-grid-fill",
                 embedded_query=True),
    # ---------- footer (canonical order: Prev, Save Changes, Save & Next, Skip, Save, Pending) ----------
    ReviewButton(key="prev", label="Prev", position="footer", order=10, kind="submit",
                 action="save_prev", css_class="btn btn-outline-secondary btn-sm flex-fill",
                 icon="bi bi-chevron-left"),
    ReviewButton(key="save", label="Save Changes", position="footer", order=20, kind="submit",
                 action="save", css_class="btn btn-primary flex-fill", lock="data-lock-save"),
    ReviewButton(key="save_next", label="Save & Next", position="footer", order=30, kind="submit",
                 action="save_next", css_class="btn btn-success flex-fill",
                 icon="bi bi-chevron-right", locked_label="Next"),
    ReviewButton(key="skip", label="Skip", position="footer", order=40, kind="submit",
                 action="save_next", css_class="btn btn-warning btn-sm text-white",
                 title="Skip", lock="data-lock-save", separator_before=True),
    ReviewButton(key="save_stay", label="Save", position="footer", order=50, kind="js",
                 action="save_stay", dom_id="saveStayFooter", css_class="save-stay-btn",
                 icon="bi bi-floppy2-fill", title="Save changes and stay on this page",
                 lock="package", separator_before=True),
    ReviewButton(key="approve_toggle", label="Pending", position="footer", order=60, kind="js",
                 action="save_toggle", dom_id="approveToggleFooter", css_class="approve-toggle-btn",
                 lock="package"),
]
