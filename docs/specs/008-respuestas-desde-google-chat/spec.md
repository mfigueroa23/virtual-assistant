# Spec 008 — Consultas a Marco y respuestas desde Google Chat en WhatsApp

- **Status:** Approved
- **Date:** 2026-10-21
- **Scope:** virtual-assistant — canal WhatsApp (specs 006 y 007), avisos y mensajes de Google Chat (spec 001)

## Por qué

Cuando el asistente de WhatsApp no tiene la información que le piden, hoy solo puede derivar: avisa a Marco y deja el
chat en pausa. Para una duda puntual («¿trabajaste con Kafka?», «¿tienes disponibilidad en enero?») eso es demasiado:
el contacto queda sin atención aunque el resto de la conversación la pueda llevar el asistente, y Marco tiene que
abrir WhatsApp en el celular para contestar algo que podría resolver en una línea desde Google Chat, donde ya le llegan
los avisos.

Además, Marco recibe los avisos de derivación y de encargos en Google Chat, pero para responder al contacto tiene que
cambiar al celular. Y la respuesta que el asistente da al derivar o al aceptar un encargo suena robótica: el sistema le
sugiere una frase hecha («lo revisarás y escribirás pronto») y el modelo la repite casi igual en cada chat.

## Decisiones

| Tema | Decisión |
|---|---|
| Cómo responde Marco | Respondiendo en el hilo del aviso, en Google Chat. Lo que escribe fuera de los hilos de avisos sigue yendo al «Agente Interno». |
| Qué recibe el contacto | Un mensaje que el LLM redacta a partir de la respuesta de Marco, en el tono de la conversación (familiar o profesional), sin agregar ni quitar datos. |
| Conversación mientras Marco no responde una consulta | El asistente sigue respondiendo, sin pausa. Lo desconocido se consulta en vez de derivarse; derivar queda para reuniones, precios, urgencias y cuando el contacto insiste en hablar con Marco. |
| Qué avisos aceptan respuesta | Todos los avisos de WhatsApp: consultas, derivaciones y encargos. |
| Ventana de 24 h cerrada | No se envía; el sistema responde en el hilo que la ventana cerró, con el enlace `wa.me` para escribir desde el celular. Sin plantillas. |
| Después de responder desde el hilo | El asistente se reanuda: si el chat estaba en pausa, la pausa se levanta, y la respuesta de Marco queda en el historial como contexto. |
| Respuesta natural del asistente | Al derivar, aceptar un encargo o consultar, el asistente responde con sus palabras; el sistema le da el criterio y nunca una frase para copiar (pedido por Marco). |
| Pausa levantada | Se levanta cualquier pausa activa del chat, sea por derivación o por un mensaje de Marco desde el celular, porque responder desde el hilo expresa que el asistente debe seguir (decidido por defecto). |
| Confirmación en el hilo | El sistema responde en el hilo con el texto que recibió el contacto, para que Marco vea la versión redactada (decidido por defecto). |
| Fallo al redactar o enviar | No se envía nada ni se envía el texto literal de Marco, que puede estar dirigido al asistente («dile que sí»); el sistema lo dice en el hilo (decidido por defecto). |
| Varias respuestas en un hilo | Cada mensaje de Marco en el hilo se redacta y envía por separado, mientras la ventana siga abierta (decidido por defecto). |
| Contenido del aviso de consulta | Nombre, «(familia)» si corresponde, número, enlace `wa.me` y la pregunta, sin resumen del LLM (decidido por defecto). |
| Varias acciones en un turno | Consultas, derivaciones y encargos del mismo turno van en un solo aviso; la pausa depende solo de la derivación, como en la spec 007 (decidido por defecto). |
| Adjuntos en la respuesta de Marco | Solo texto; un mensaje sin texto no se envía y el sistema lo indica en el hilo (decidido por defecto). |
| Vencimiento | Un hilo acepta respuestas mientras la ventana de 24 h del contacto esté abierta, sin otro vencimiento (decidido por defecto). |

## Requisitos funcionales

"El sistema" es la API del asistente; "contacto", "Marco", "lote", "pausa", "aviso", "space de aviso", "derivar",
"encargo" y "familiar" son los de las specs 006 y 007. "Consultar" es la acción del coordinador de WhatsApp que pregunta
a Marco algo que el asistente no sabe responder; "hilo del aviso" es el hilo de Google Chat que abre cada aviso de
WhatsApp en el space de aviso; "respuesta desde el hilo" es un mensaje que Marco escribe dentro de ese hilo; "ventana
de 24 h" es el plazo desde el último mensaje del contacto en que WhatsApp permite enviarle texto libre.

### Consultar a Marco

- **RF-01** — El sistema debe ofrecer al coordinador de WhatsApp una acción para consultar a Marco una pregunta que no
  sabe responder, indicando la pregunta.
- **RF-02** — El sistema no debe ofrecer la acción de consultar a los coordinadores del chat web ni de Google Chat.
- **RF-03** — Cuando el coordinador consulte en un turno, el sistema debe enviar al contacto la respuesta de ese turno
  igual que cualquier otra respuesta del canal.
- **RF-04** — Cuando el coordinador consulte en un turno, el sistema debe publicar un aviso en el space de aviso con la
  pregunta, el nombre de perfil del contacto (o «Sin nombre»), su número, el enlace `https://wa.me/<número sin el
  signo +>` y la indicación «(familia)» si el contacto es familiar.
- **RF-05** — Cuando el coordinador consulte sin derivar en el mismo turno, el sistema no debe pausar la conversación.
- **RF-06** — Si en el mismo turno el coordinador consulta más de una vez, o además deriva o registra encargos, entonces
  el sistema debe publicar un solo aviso con todas las preguntas, motivos y encargos, y pausar solo si hubo derivación.
- **RF-07** — Si la respuesta del turno se descarta porque Marco escribió durante el lote, entonces el sistema no debe
  publicar el aviso de la consulta.
- **RF-08** — Si el aviso de un turno con consultas y sin derivación no se publica después de los reintentos de la spec
  007, entonces el sistema debe registrar el error en el log con el número del contacto y sin la pregunta.

### Respuesta natural del asistente

- **RF-09** — El sistema no debe entregar al coordinador de WhatsApp frases textuales para responder al contacto cuando
  deriva, registra un encargo o consulta.
- **RF-10** — Cuando el coordinador derive, registre un encargo o consulte, el sistema debe indicarle que responda con
  sus propias palabras, según lo que pidió el contacto, sin prometer plazos ni confirmar lo que Marco debe decidir.

### Responder desde el hilo del aviso

- **RF-11** — El sistema debe incluir en cada aviso de WhatsApp (consulta, derivación o encargos) la indicación de que
  responder en su hilo envía la respuesta al contacto.
- **RF-12** — Cuando Marco escriba un texto en el hilo de un aviso de WhatsApp, el sistema debe enviar al contacto de
  ese aviso, por WhatsApp, un mensaje redactado por el LLM a partir del texto de Marco, en el tono de la conversación
  y sin agregar ni quitar datos.
- **RF-13** — Cuando Marco escriba en el space de aviso fuera del hilo de un aviso de WhatsApp, el sistema debe
  responder con el «Agente Interno», como hasta ahora.
- **RF-14** — Cuando el mensaje se envíe al contacto, el sistema debe responder en el hilo con el texto que recibió el
  contacto.
- **RF-15** — Cuando el mensaje se envíe al contacto, el sistema debe sumarlo al historial de la conversación de
  WhatsApp como un mensaje de Marco.
- **RF-16** — Cuando el mensaje se envíe al contacto, el sistema debe levantar la pausa de esa conversación, si estaba
  activa.
- **RF-17** — Cuando Marco escriba varios mensajes en el mismo hilo, el sistema debe redactar y enviar cada uno por
  separado.
- **RF-18** — El sistema debe aceptar respuestas en el hilo de un aviso publicado antes de un reinicio de la API.
- **RF-19** — Si pasaron más de 24 h desde el último mensaje del contacto, entonces el sistema no debe enviar nada y
  debe responder en el hilo que la ventana de WhatsApp está cerrada, con el enlace `https://wa.me/<número sin el
  signo +>`.
- **RF-20** — Si el LLM falla o supera el tiempo máximo al redactar el mensaje, entonces el sistema no debe enviar nada
  al contacto y debe responder en el hilo que no se pudo enviar, sin cambiar el historial ni la pausa.
- **RF-21** — Si YCloud rechaza el mensaje o no se logra conectar, entonces el sistema debe responder en el hilo que no
  se pudo enviar, sin cambiar el historial ni la pausa.
- **RF-22** — Si el mensaje de Marco en el hilo no tiene texto, entonces el sistema no debe enviar nada y debe responder
  en el hilo que solo se envían respuestas de texto.
- **RF-23** — Mientras el canal WhatsApp esté deshabilitado, el sistema no debe enviar respuestas desde el hilo y debe
  responder en el hilo que el canal está apagado.
- **RF-24** — El sistema no debe registrar en el log el texto de Marco ni el mensaje enviado al contacto cuando falle
  una respuesta desde el hilo.

## Fuera de alcance

- Responder con un botón en una tarjeta, o en un space aparte para los avisos: descartados; se responde en el hilo.
- Enviar el texto de Marco tal cual, o con una marca para elegir: descartado; siempre lo redacta el LLM (RF-12).
- Pausar el chat mientras Marco no responde una consulta: descartado; el asistente sigue respondiendo (RF-05).
- Pausar el chat al responder desde el hilo: descartado; el asistente se reanuda (RF-16).
- Plantillas de WhatsApp para responder fuera de la ventana de 24 h: descartadas (RF-19).
- Enviar el texto literal de Marco cuando falla la redacción: descartado (RF-20).
- Enviar archivos, imágenes o audios desde Google Chat al contacto.
- Recordatorios a Marco cuando una consulta lleva tiempo sin respuesta, y vencimiento de los hilos distinto de la
  ventana de 24 h.
- Editar o borrar en WhatsApp un mensaje ya enviado desde el hilo.
- Proponer y crear preguntas frecuentes a partir de las respuestas de Marco: fase 4.

## Restricciones

- **Aprobación de esquema pendiente:** responder desde el hilo exige recordar a qué contacto pertenece cada aviso,
  también después de un reinicio (RF-18), y saber cuándo escribió el contacto por última vez (RF-19). El plan definirá
  las columnas o tablas nuevas y su migración.
- **Cambia la spec 007:** «pregunta algo que no está en la información disponible» deja de ser un motivo para derivar y
  pasa a ser una consulta. Cambian la descripción de la acción de derivar, las guías que reciben las acciones (RF-09,
  RF-10) y el prompt del «Agente WhatsApp», que hoy trae frases de ejemplo literales. El cambio del prompt es un dato:
  el README documenta su `UPDATE` y Marco lo revisa antes de aplicarlo.
- **Cambia la spec 001:** los mensajes de Marco dentro del hilo de un aviso de WhatsApp dejan de ir al «Agente Interno».
  El resto de los mensajes de Google Chat no cambia (RF-13).
- **Google Chat:** el diseño depende de que Google Chat permita responder en el hilo de un mensaje de la app dentro del
  mensaje directo con la app, y de que el evento traiga el hilo. Se comprueba al inicio del plan; si no se puede, la
  forma de responder vuelve a decidirse.
- **WhatsApp:** fuera de la ventana de 24 h la API solo permite plantillas aprobadas; desde la app del celular Marco
  puede escribir siempre.
- **Sin dependencias nuevas** previstas.
- Constitución: §4 (tests sin BD real, sin LLM, sin YCloud y sin Google), §6 (configuración en `property`), §7 (sin
  trazas ni detalles internos en las respuestas), §9 (versión al cerrar la spec).

## Criterios de finalización

- [ ] Cada RF tiene al menos un test en `tests/` que pasa con `uv run pytest`, sin BD real, sin LLM y sin llamadas reales
      a YCloud ni a Google.
- [ ] `uv run pyright` termina con 0 errores, `uv run alembic check` no reporta diferencias y las migraciones del
      esquema aprobado quedan aplicadas.
- [ ] README documenta las consultas, cómo responder desde el hilo, la ventana de 24 h y el `UPDATE` del prompt del
      «Agente WhatsApp» sin frases literales.
- [ ] Versión minor subida en `pyproject.toml` y `uv sync` ejecutado, en un solo commit con `uv.lock`.
- [ ] Verificación manual desde otro teléfono:
  - una pregunta que el asistente no sabe recibe una respuesta propia del asistente, sin frase hecha, y llega a Google
    Chat un aviso con la pregunta;
  - el asistente sigue respondiendo otras preguntas del mismo chat;
  - responder en el hilo del aviso hace llegar al contacto un mensaje redactado con esa información, y el hilo muestra
    el texto enviado;
  - responder en el hilo de una derivación levanta la pausa y el asistente vuelve a responder ese chat;
  - un mensaje fuera de los hilos sigue respondiéndolo el «Agente Interno»;
  - tres derivaciones distintas (reunión, precio, urgencia) reciben respuestas redactadas de forma distinta.
