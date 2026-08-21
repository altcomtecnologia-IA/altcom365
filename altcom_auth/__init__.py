"""
altcom_auth — módulo de autenticação e autorização da plataforma Altcom.

Uso no app Flask:
    from altcom_auth import registrar, requer
    registrar(app)                      # liga o middleware

    @app.route('/laudo/exportar')
    @requer('laudo:exportar')           # exige capacidade
    def exportar(): ...

Interruptor de emergência:
    AUTH_ENABLED=false → middleware inativo, app volta ao comportamento anterior.
"""
from .middleware import registrar
from .decorators import requer

__all__ = ['registrar', 'requer']
