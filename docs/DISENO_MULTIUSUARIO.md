# Diseño: dos usuarios con identidad (responsable administrativo y contador)

Este documento es solo diseño. No se agrega código de autenticación en esta
entrega; el objetivo es dejar un plan concreto y de bajo riesgo para cuando
se decida implementarlo, sin desplegar la aplicación fuera de `localhost`
hasta completarlo.

## Por qué ahora es solo diseño

El dominio (`Account.add_movement`, `approve_movement`, `discard_movement`,
`correct_movement`, `reverse_movement` en
[domain.py](../src/cuentas_corrientes/domain.py)) ya recibe un parámetro
`actor: str` en cada operación y lo registra en `AuditEvent`. Hoy la API
pasa siempre el valor fijo `"api-user"`. Esto significa que agregar
identidad real es un cambio localizado — reemplazar ese valor fijo por el
usuario autenticado — y no una reestructuración del motor. Por eso conviene
diseñarlo con cuidado pero no apurar la implementación de sesiones,
contraseñas y CSRF en la misma entrega que reescribe la interfaz.

## Modelo de datos propuesto

Nueva tabla `users` (SQLite, mismo archivo o uno separado fuera del repo):

| Campo | Tipo | Notas |
|---|---|---|
| `id` | TEXT (uuid) | clave primaria |
| `username` | TEXT único | ej. `administracion`, `contador` |
| `display_name` | TEXT | nombre real para mostrar en auditoría |
| `password_hash` | TEXT | hash con `bcrypt`, nunca la contraseña en texto plano |
| `created_at` | TEXT (ISO) | |
| `is_active` | BOOLEAN | permite deshabilitar sin borrar históricos |

Ambos usuarios tienen **los mismos permisos funcionales** (no hay
jerarquía): cualquiera puede crear cuentas, cargar información, corregir,
aprobar, denegar y revertir. Lo único que cambia es que cada acción queda
firmada con la identidad de quien la hizo.

Migración del `actor`: en `api.py`, cada llamada a un método del dominio
deja de pasar `"api-user"` y pasa `current_user.display_name` (obtenido de
la sesión). El formato de `AuditEvent` no cambia — sigue siendo
`(action, actor, entity_id, at, detail)` — así que el historial existente
sigue siendo válido y comparable con el nuevo.

## Autenticación y sesiones

- **Login**: `POST /auth/login` con `username` + `password`. Verifica el
  hash con `bcrypt.checkpw`. Si es correcto, crea una fila en una tabla
  `sessions` (`id` aleatorio de 32 bytes, `user_id`, `created_at`,
  `expires_at`) y la entrega como cookie `HttpOnly`, `Secure` (cuando haya
  HTTPS), `SameSite=Lax`.
- **Verificación por request**: middleware de FastAPI que busca la cookie
  de sesión, la valida contra la tabla `sessions` (no vencida) y expone
  `request.state.user`. Los endpoints existentes toman el actor de ahí en
  vez de un valor fijo.
- **Logout**: `POST /auth/logout` borra la fila de `sessions` y limpia la
  cookie.
- **Contraseñas**: hash con `bcrypt` (costo ≥ 12). Nunca se guardan ni se
  loguean en texto plano. Cambiar contraseña exige la actual.
- **Rate limiting de login**: contador simple en memoria o en SQLite por
  `username` (ej. 5 intentos fallidos → bloqueo de 5 minutos) para frenar
  fuerza bruta local.

## CSRF

Con cookies de sesión, cualquier POST del navegador necesita protección
CSRF. Patrón recomendado (doble cookie, sin dependencias nuevas más allá de
`bcrypt`):

1. Al iniciar sesión, además de la cookie de sesión, se entrega una cookie
   `csrf_token` (no `HttpOnly`, para que el JavaScript de la interfaz pueda
   leerla).
2. La interfaz envía ese valor en un header `X-CSRF-Token` en cada
   `POST`/`PATCH`/`DELETE`.
3. El middleware compara cookie vs. header; si no coinciden, devuelve 403.

## Dependencia nueva a agregar en esa etapa

- `bcrypt` (paquete pequeño, sin dependencias transitivas pesadas,
  estándar de facto para hash de contraseñas en Python). Se documentará en
  `pyproject.toml` junto con la justificación de por qué se eligió sobre
  alternativas de la librería estándar (`hashlib.scrypt` es viable pero
  `bcrypt` es más simple de usar correctamente y ampliamente auditado).

## Checklist antes de exponer la aplicación fuera de `localhost`

1. **HTTPS obligatorio**: terminar TLS con un reverse proxy (Caddy, nginx)
   o un túnel administrado; nunca servir sesiones o contraseñas en HTTP
   plano.
2. **Secretos fuera del repositorio**: claves de sesión, cualquier clave de
   firma, y credenciales de despliegue van en variables de entorno o en un
   `.env` local, nunca en Git (ya excluido vía `.gitignore`).
3. **Base de datos y backups fuera de GitHub**: ya es el caso
   (`data/` y `backups/` están en `.gitignore`); para producción, además
   moverlos fuera del árbol de trabajo del repositorio (ej.
   `~/cuentas-corrientes-data/`) para que un `git clean` o un error de
   configuración no pueda tocarlos.
4. **Backup verificado inmediatamente antes de publicar un enlace**:
   correr `scripts/restore.py --list` para confirmar que existe un backup
   reciente y que `restore_backup` (ver [backup.py](../src/cuentas_corrientes/backup.py))
   pasa la verificación de integridad.
5. **Usuarios creados y probados**: alta de ambos usuarios reales,
   verificar que cada acción en la auditoría queda firmada con la persona
   correcta, y que un usuario sin sesión válida no puede llamar a ningún
   endpoint de escritura.
6. **Revisión de CORS**: si la interfaz y la API siguen serviéndose desde
   el mismo origen (como hoy), no hace falta configurar CORS; si se
   separan, restringir el origen permitido explícitamente.

## Qué no cambia con este diseño

- El motor determinístico de saldos no se toca.
- La API sigue devolviendo los mismos campos; solo se agrega la capa de
  sesión por delante y se reemplaza el actor fijo por el actor real.
- La interfaz solo necesita agregar una pantalla de login y enviar el
  header CSRF en sus llamadas — no requiere reescritura.
