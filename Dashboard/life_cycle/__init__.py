"""Life Cycle Assessment blueprint package.

Import ``life_cycle_bp`` and register it on the portal app under the
``/life-cycle`` URL prefix:

    from life_cycle import life_cycle_bp
    app.register_blueprint(life_cycle_bp, url_prefix="/life-cycle")
"""

from .blueprint import life_cycle_bp

__all__ = ["life_cycle_bp"]
