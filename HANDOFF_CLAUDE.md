# Traspaso para Claude Code — Agente de cuentas corrientes

## Objetivo inmediato

Convertir el prototipo actual en un MVP local realmente operable por una persona no tecnica. No procesar datos reales ni desplegar en Internet hasta completar los controles de seguridad indicados abajo.

## Estado comprobado

- Motor deterministico de movimientos, saldos confirmados/proyectados y monedas separadas.
- Ingreso inicial de texto conversacional con preguntas cuando falta informacion.
- Aprobacion humana de propuestas.
- Descarte trazable de propuestas pendientes.
- Persistencia local SQLite en `data/cuentas.sqlite3` (excluida de Git).
- Interfaz local en `/`.
- Ocho pruebas automatizadas aprobadas.

## Problema actual

La interfaz es demasiado basica y el flujo queda poco claro para el usuario. No debe considerarse lista para una cuenta real. Abrir el archivo HTML directamente con `file://` tampoco funciona; la aplicacion debe servirse por HTTP local.

## Prioridad 1: experiencia completa y simple

Implementar y verificar en navegador un flujo de principio a fin:

1. Crear y seleccionar una cuenta.
2. Cargar texto ficticio.
3. Mostrar claramente que interpreto el sistema antes de guardarlo.
4. Permitir corregir campos de la propuesta.
5. Permitir aprobar o denegar con motivo.
6. Permitir revertir un movimiento confirmado mediante un contramovimiento trazable; nunca borrarlo.
7. Mostrar saldo inicial, movimientos, anticipos sin asignar, saldo confirmado, saldo proyectado y saldo final.
8. Mostrar errores en lenguaje sencillo y evitar que la pantalla quede en un estado inconcluso.

## Prioridad 2: confiabilidad local

- Agregar pruebas end-to-end de la interfaz y API.
- Manejar errores de red y respuestas inesperadas.
- Evitar HTML generado con datos sin escapar.
- Agregar copia de seguridad local automática y restauracion comprobada.
- Agregar una orden simple de inicio; idealmente un solo comando o lanzador local.
- No agregar dependencias sin justificar y documentar su necesidad.

## Prioridad 3: preparacion para dos usuarios

Diseñar, pero no desplegar todavia:

- Usuario del responsable administrativo y usuario del contador.
- Iguales permisos funcionales.
- Identidad individual en cada alta, correccion, aprobacion o descarte.
- Sesiones seguras, contraseñas con hash y proteccion CSRF.
- Base y documentos fuera de GitHub.
- HTTPS, secretos externos al repositorio y copias de seguridad antes de publicar un enlace.

## Reglas comerciales ya aprobadas

- El sistema debe servir para cualquier cuenta corriente, no solo fletes.
- Una factura prevalece para peso, tarifa e IVA cuando existe.
- Sin factura no se calcula IVA.
- Los fletes sin factura pueden usar toneladas manuales aprobadas o bruto menos tara de una captura.
- Los anticipos de gasoil usan el precio de la ultima factura de gasoil pagada por la empresa; si falta, preguntar y no calcular.
- Un anticipo sin referencia especifica queda como anticipo general no asignado.
- No distribuir anticipos automaticamente entre facturas antiguas.
- Toda ambiguedad material requiere revision humana.
- La IA puede extraer y proponer; el motor deterministico calcula el saldo oficial.

## Seguridad obligatoria

- No leer ni incorporar datos reales durante el desarrollo inicial.
- No subir bases SQLite, `.env`, documentos, conversaciones ni credenciales.
- No pedir contraseñas, tokens, claves API, codigos SMS ni claves privadas por chat.
- No crear servicios externos, instalar software o hacer push sin autorizacion especifica del usuario.
- Mantener el repositorio privado.

## Criterio de terminado para la proxima entrega

El usuario debe poder abrir la aplicacion con un procedimiento corto, completar un caso ficticio entero sin ayuda tecnica, aprobar/denegar/corregir/revertir y cerrar/reabrir sin perder datos. Todas las pruebas deben pasar y debe entregarse un resumen de archivos cambiados, riesgos pendientes y pasos exactos para probar.
