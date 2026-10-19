# Spec 003 — Aviso de espera redactado por el LLM en Google Chat

- **Status:** Draft
- **Date:** 2026-10-19
- **Scope:** virtual-assistant — endpoint de eventos de Google Chat (respuesta diferida de la spec 001)

## Por qué

Cuando el asistente tarda más que el umbral de aviso en responder en Google Chat, el usuario recibe siempre el mismo
texto fijo («Estoy revisando tu solicitud. Te enviaré la respuesta en un mensaje nuevo cuando esté lista»). El texto
no dice qué se está revisando, suena mecánico frente al resto de la conversación y cambiar su tono exige modificar el
código y redesplegar.

Se quiere que el aviso lo formule el propio LLM a partir del mensaje del usuario, de modo que confirme qué se está
atendiendo, y que sus instrucciones se puedan ajustar sin redeploy, sin poner en riesgo que el aviso llegue a tiempo.

## Decisiones

| Tema | Decisión |
|---|---|
| Contexto del LLM | Solo el mensaje actual del usuario y los nombres de sus adjuntos; sin historial y sin leer los archivos. |
| Momento de generación | Solo cuando el procesamiento supera el umbral de aviso; las respuestas rápidas no hacen llamadas extra. |
| Fallback | Si el LLM falla, devuelve texto vacío o supera un tiempo límite configurable, se envía el aviso fijo actual y se registra en el log. |
| Instrucciones del aviso | Nueva property en la tabla `property`, editable sin redeploy; si falta, se usa el aviso fijo. |
| Tiempo límite del aviso | Nueva property en segundos con valor por defecto 5 si falta (decidido por defecto). |
| Alcance | Solo el aviso; `DEMORA` y `NO_DISPONIBLE` siguen siendo textos fijos. |
| Respuesta lista mientras se redacta el aviso | Se responde en el hilo con la respuesta y el aviso se descarta (decidido por defecto). |
| Historial | El aviso no se guarda en el historial de la conversación, como hoy (decidido por defecto). |

## Requisitos funcionales

"El sistema" es la API del asistente en su endpoint de eventos de Google Chat; "umbral de aviso" y "tiempo máximo
total" son los de la spec 001; "aviso fijo" es el texto actual «Estoy revisando tu solicitud. Te enviaré la respuesta
en un mensaje nuevo cuando esté lista»; "tiempo límite del aviso" es el nuevo parámetro configurable (5 s por defecto).

### Generación del aviso

- **RF-01** — Cuando el procesamiento de un mensaje supere el umbral de aviso, el sistema debe solicitar al LLM un
  aviso en español a partir de las instrucciones configuradas, el texto del mensaje y los nombres de sus adjuntos.
- **RF-02** — Cuando el LLM devuelva el aviso dentro del tiempo límite del aviso, el sistema debe responder al evento
  de Google Chat con ese texto en lugar del aviso fijo.
- **RF-03** — Cuando el asistente obtenga la respuesta antes del umbral de aviso, el sistema debe responder en el
  hilo sin solicitar ningún aviso al LLM.
- **RF-04** — Mientras se redacta el aviso, el sistema debe continuar procesando la solicitud, contando el tiempo
  máximo total desde la llegada del evento.
- **RF-05** — Cuando la respuesta del asistente quede lista antes que el aviso, el sistema debe responder en el hilo
  con la respuesta, descartar el aviso y no publicar ningún mensaje diferido.
- **RF-06** — El sistema debe redactar el aviso sin leer el contenido de los adjuntos ni el historial de la
  conversación.

### Configuración

- **RF-07** — El sistema debe leer las instrucciones del aviso y el tiempo límite del aviso desde la configuración en
  base de datos en cada evento, de modo que un cambio aplique desde el siguiente evento.
- **RF-08** — Si el tiempo límite del aviso no está configurado, entonces el sistema debe usar 5 segundos.

### Fallback al aviso fijo

- **RF-09** — Si las instrucciones del aviso no están configuradas, entonces el sistema debe responder con el aviso
  fijo sin llamar al LLM.
- **RF-10** — Si el LLM no devuelve el aviso dentro del tiempo límite del aviso, entonces el sistema debe responder
  con el aviso fijo y registrar la demora en el log.
- **RF-11** — Si el LLM o su configuración fallan al redactar el aviso, entonces el sistema debe responder con el aviso
  fijo, registrar el error en el log sin exponer detalles al usuario y continuar con la respuesta diferida.
- **RF-12** — Si el LLM devuelve un aviso vacío o solo con espacios, entonces el sistema debe responder con el aviso
  fijo.

### Integración con la respuesta diferida

- **RF-13** — Cuando se haya enviado un aviso (redactado o fijo), el sistema debe publicar la respuesta final como
  mensaje nuevo según la spec 001, sin cambios en mención, hilo ni historial.
- **RF-14** — El sistema debe entregar por cada solicitud como máximo un aviso y un único mensaje final (respuesta,
  demora o error).
- **RF-15** — El sistema no debe guardar el aviso en el historial de la conversación.

## Fuera de alcance

- Redactar con el LLM el mensaje de demora (`DEMORA`), el de servicio no disponible (`NO_DISPONIBLE`) o la
  bienvenida: descartado; salen cuando algo falló, a menudo el propio LLM.
- Generar el aviso en paralelo desde el inicio de cada mensaje: descartado por el costo de una llamada extra por mensaje.
- Usar el historial de la conversación o el contenido de los adjuntos para redactar el aviso: descartado por latencia.
- Reutilizar el prompt del coordinador o fijar las instrucciones en código: descartado; van en una property.
- Editar o borrar el aviso al llegar la respuesta; avisos múltiples o progresivos.
- El endpoint `/chat` y otros canales.
- Cambios en `src/models/` y nuevas dependencias.

## Restricciones

- Sin cambio de esquema: las dos properties nuevas son filas de la tabla `property` existente y se documentan en el
  README.
- El umbral de aviso más el tiempo límite del aviso debe quedar por debajo del límite de ~30 s de Google Chat; el README
  debe advertirlo.
- Constitución: §4 (tests sin BD real ni LLM), §6 (parámetros por configuración, no hardcodeados), §7 (sin trazas al
  usuario), §8 (respuestas en español), §9 (subida de versión al cerrar la spec).

## Criterios de finalización

- [ ] Cada RF tiene al menos un test en `tests/` que pasa con `uv run pytest`, sin BD real, sin LLM y sin llamadas
      reales a Google.
- [ ] `uv run pyright` termina con 0 errores y `uv run alembic check` no reporta diferencias.
- [ ] README documenta las dos properties nuevas, su valor por defecto y la advertencia del límite de Google Chat.
- [ ] Versión minor subida en `pyproject.toml` y `uv sync` ejecutado, en un solo commit con `uv.lock`.
- [ ] Verificación manual en Google Chat: un mensaje lento con adjunto recibe un aviso que menciona lo solicitado;
      sin la property de instrucciones (o con un tiempo límite muy bajo) recibe el aviso fijo; en ambos casos llega
      después la respuesta como mensaje nuevo.
