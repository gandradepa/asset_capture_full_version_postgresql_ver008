(function () {
    "use strict";

    var DEFAULTS = {
        stageSelector: ".main-stage",
        imageSelector: "#mainImage",
        zoomInSelector: "#zoomIn",
        zoomOutSelector: "#zoomOut",
        rotateSelector: "#rotate",
        resetSelector: "#resetViewer",
        thumbSelector: ".thumb",
        minScale: 0.5,
        maxScale: 6,
        buttonStep: 0.2,
        wheelSpeed: 0.0015,
        doubleClickScale: 2.2,
        panStep: 40
    };

    function mergeOptions(options) {
        var merged = {};
        Object.keys(DEFAULTS).forEach(function (key) {
            merged[key] = DEFAULTS[key];
        });
        Object.keys(options || {}).forEach(function (key) {
            merged[key] = options[key];
        });
        return merged;
    }

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function normalizeRotation(degrees) {
        var value = degrees % 360;
        return value < 0 ? value + 360 : value;
    }

    function hasVisibleImage(img) {
        return !!(img && img.getAttribute("src") && img.offsetParent !== null);
    }

    function init(options) {
        var settings = mergeOptions(options);
        var stage = document.querySelector(settings.stageSelector);
        var img = document.querySelector(settings.imageSelector);
        var zoomIn = document.querySelector(settings.zoomInSelector);
        var zoomOut = document.querySelector(settings.zoomOutSelector);
        var rotateButton = document.querySelector(settings.rotateSelector);
        var resetButton = document.querySelector(settings.resetSelector);

        if (!stage || !img) {
            return null;
        }

        var state = {
            scale: 1,
            rotation: 0,
            translateX: 0,
            translateY: 0,
            dragging: false,
            pointerId: null,
            startClientX: 0,
            startClientY: 0,
            startTranslateX: 0,
            startTranslateY: 0
        };

        function transformedSize() {
            var baseWidth = img.offsetWidth || 0;
            var baseHeight = img.offsetHeight || 0;
            var quarterTurn = normalizeRotation(state.rotation) % 180 !== 0;
            return {
                width: (quarterTurn ? baseHeight : baseWidth) * state.scale,
                height: (quarterTurn ? baseWidth : baseHeight) * state.scale
            };
        }

        function clampPan() {
            var size = transformedSize();
            var maxX = Math.max(0, (size.width - stage.clientWidth) / 2);
            var maxY = Math.max(0, (size.height - stage.clientHeight) / 2);

            state.translateX = clamp(state.translateX, -maxX, maxX);
            state.translateY = clamp(state.translateY, -maxY, maxY);

            if (maxX === 0) state.translateX = 0;
            if (maxY === 0) state.translateY = 0;

            stage.classList.toggle("can-pan", maxX > 0 || maxY > 0);
        }

        function applyTransform() {
            clampPan();
            img.style.transform = "translate3d(" + state.translateX + "px, " + state.translateY + "px, 0) scale(" + state.scale + ") rotate(" + state.rotation + "deg)";
        }

        function reset() {
            state.scale = 1;
            state.rotation = 0;
            state.translateX = 0;
            state.translateY = 0;
            applyTransform();
        }

        function zoomAt(nextScale, clientX, clientY) {
            if (!hasVisibleImage(img)) return;

            var previousScale = state.scale;
            var scale = clamp(nextScale, settings.minScale, settings.maxScale);
            if (scale === previousScale) return;

            if (typeof clientX === "number" && typeof clientY === "number") {
                var rect = stage.getBoundingClientRect();
                var pivotX = clientX - rect.left - rect.width / 2;
                var pivotY = clientY - rect.top - rect.height / 2;
                var ratio = scale / previousScale;
                state.translateX = pivotX - (pivotX - state.translateX) * ratio;
                state.translateY = pivotY - (pivotY - state.translateY) * ratio;
            }

            state.scale = scale;
            applyTransform();
        }

        function zoomBy(delta, clientX, clientY) {
            zoomAt(state.scale + delta, clientX, clientY);
        }

        function rotate() {
            if (!hasVisibleImage(img)) return;
            state.rotation = normalizeRotation(state.rotation + 90);
            applyTransform();
        }

        function panBy(deltaX, deltaY) {
            if (!hasVisibleImage(img)) return;
            state.translateX += deltaX;
            state.translateY += deltaY;
            applyTransform();
        }

        function stageCenter() {
            var rect = stage.getBoundingClientRect();
            return {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2
            };
        }

        function onWheel(event) {
            if (!hasVisibleImage(img)) return;
            event.preventDefault();

            var factor = Math.exp(-event.deltaY * settings.wheelSpeed);
            zoomAt(state.scale * factor, event.clientX, event.clientY);
        }

        function onPointerDown(event) {
            if (!hasVisibleImage(img)) return;
            if (event.button !== 0 || event.target.closest(".viewer-controls")) return;

            var size = transformedSize();
            var canPan = size.width > stage.clientWidth || size.height > stage.clientHeight;
            if (!canPan) return;

            state.dragging = true;
            state.pointerId = event.pointerId;
            state.startClientX = event.clientX;
            state.startClientY = event.clientY;
            state.startTranslateX = state.translateX;
            state.startTranslateY = state.translateY;

            stage.classList.add("is-dragging");
            stage.setPointerCapture(event.pointerId);
            event.preventDefault();
        }

        function onPointerMove(event) {
            if (!state.dragging || event.pointerId !== state.pointerId) return;

            state.translateX = state.startTranslateX + event.clientX - state.startClientX;
            state.translateY = state.startTranslateY + event.clientY - state.startClientY;
            applyTransform();
        }

        function endDrag(event) {
            if (!state.dragging || event.pointerId !== state.pointerId) return;

            state.dragging = false;
            state.pointerId = null;
            stage.classList.remove("is-dragging");

            try {
                stage.releasePointerCapture(event.pointerId);
            } catch (err) {
                // Pointer capture can already be released if the pointer is cancelled.
            }
        }

        function onDoubleClick(event) {
            if (!hasVisibleImage(img) || event.target.closest(".viewer-controls")) return;
            if (state.scale <= 1.01 && state.translateX === 0 && state.translateY === 0) {
                zoomAt(settings.doubleClickScale, event.clientX, event.clientY);
            } else {
                reset();
            }
        }

        function onKeyDown(event) {
            if (event.target !== stage || !hasVisibleImage(img)) return;

            var center = stageCenter();
            var handled = true;

            if (event.key === "+" || event.key === "=") {
                zoomBy(settings.buttonStep, center.x, center.y);
            } else if (event.key === "-" || event.key === "_") {
                zoomBy(-settings.buttonStep, center.x, center.y);
            } else if (event.key === "0") {
                reset();
            } else if (event.key === "r" || event.key === "R") {
                rotate();
            } else if (event.key === "ArrowLeft") {
                panBy(settings.panStep, 0);
            } else if (event.key === "ArrowRight") {
                panBy(-settings.panStep, 0);
            } else if (event.key === "ArrowUp") {
                panBy(0, settings.panStep);
            } else if (event.key === "ArrowDown") {
                panBy(0, -settings.panStep);
            } else {
                handled = false;
            }

            if (handled) {
                event.preventDefault();
            }
        }

        stage.addEventListener("wheel", onWheel, { passive: false });
        stage.addEventListener("pointerdown", onPointerDown);
        stage.addEventListener("pointermove", onPointerMove);
        stage.addEventListener("pointerup", endDrag);
        stage.addEventListener("pointercancel", endDrag);
        stage.addEventListener("dblclick", onDoubleClick);
        stage.addEventListener("keydown", onKeyDown);
        img.addEventListener("dragstart", function (event) {
            event.preventDefault();
        });
        img.addEventListener("load", reset);
        window.addEventListener("resize", applyTransform);

        if (zoomIn) {
            zoomIn.addEventListener("click", function () {
                var center = stageCenter();
                zoomBy(settings.buttonStep, center.x, center.y);
            });
        }

        if (zoomOut) {
            zoomOut.addEventListener("click", function () {
                var center = stageCenter();
                zoomBy(-settings.buttonStep, center.x, center.y);
            });
        }

        if (rotateButton) {
            rotateButton.addEventListener("click", rotate);
        }

        if (resetButton) {
            resetButton.addEventListener("click", reset);
        }

        if (img.complete) {
            reset();
        }

        return {
            reset: reset,
            apply: applyTransform,
            zoomIn: function () {
                var center = stageCenter();
                zoomBy(settings.buttonStep, center.x, center.y);
            },
            zoomOut: function () {
                var center = stageCenter();
                zoomBy(-settings.buttonStep, center.x, center.y);
            },
            rotate: rotate
        };
    }

    window.AssetImageViewer = {
        init: init
    };
})();
