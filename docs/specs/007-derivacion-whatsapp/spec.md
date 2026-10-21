# Spec 007 — Derivación, encargos y círculo familiar en WhatsApp

- **Status:** Approved
- **Date:** 2026-10-20
- **Scope:** virtual-assistant — canal WhatsApp (spec 006), aviso en Google Chat y conexiones salientes a YCloud y a Google Chat

## Por qué

El asistente de WhatsApp (spec 006) responde como Marco, pero hay mensajes que no debe resolver solo: le piden una
reunión o un precio, es una urgencia, no tiene la información o el contacto insiste en hablar con él. Hoy responde algo
como «déjame revisarlo y te escribo», y Marco solo se entera si abre WhatsApp y lee la conversación. Mientras tanto el
asistente sigue respondiendo ese chat, con el riesgo de contradecir lo que Marco conteste después.

Otras veces el contacto le pide a Marco que haga algo («¿me mandas tu CV?», «revisa el documento que te envié»). Para
los tipos de encargo que Marco permita, el asistente debería aceptarlo en su nombre y dejarle anotado qué tiene que
hacer y quién se lo pidió, sin cortar la conversación.

Por último, el asistente trata igual a todos los contactos. Marco quiere un trato distinto para su círculo familiar
(tono cercano y que puedan pedirle cualquier cosa) y un trato profesional para el resto.

Además, el 2026-10-20, ya desplegada la versión 3.5.0, el primer envío a YCloud falló con un error de conexión y el
contacto quedó sin respuesta. Desde el cluster, IPv6 no tiene ruta e IPv4 conecta siempre, y las fallas fueron
intermitentes en las primeras conexiones. El 2026-10-19 hubo un incidente parecido con la API de Google Chat. Como ante
un fallo el canal guarda silencio, un corte de red pasajero basta para dejar a un contacto sin respuesta.

## Decisiones

| Tema | Decisión |
|---|---|
| Después de derivar | El asistente responde al contacto, avisa a Marco y se pausa en ese chat durante la ventana de pausa de la spec 006 (12 h por defecto), igual que si Marco hubiera escrito desde el celular. |
| Aviso al pausarse por un mensaje de Marco | No: solo se avisa al derivar, porque si Marco respondió ya sabe que tomó el chat. |
| Contenido del aviso | Nombre y número del contacto, motivo, resumen breve redactado por el LLM y enlace `https://wa.me/<número>`. |
| Fallo del aviso | Se reintenta; si igual falla, queda en el log y el asistente **no** se pausa, para que el contacto no quede horas sin respuesta. |
| Alcance del fix de red | Todas las llamadas HTTP salientes de la app a YCloud y a la API de Google Chat, incluido el canal de Google Chat existente. Gemini va por su propio SDK y queda fuera. |
| Quién decide derivar | El coordinador de WhatsApp, con una acción propia y según los criterios de su prompt, que se edita en la base de datos (decidido por defecto). |
| Destino del aviso | El space de Google Chat configurado en una property, normalmente el mensaje directo de Marco con la app (decidido por defecto). |
| Reintentos | Hasta 2 reintentos tanto para publicar el aviso como para establecer cada conexión (decidido por defecto). |
| Resumen fallido | El aviso se envía igual, sin resumen (decidido por defecto). |
| Respuesta del turno | La redacta el coordinador como cualquier otra; el sistema no agrega un texto fijo (decidido por defecto, consistente con RF-15 de la spec 006). |
| Encargos | El asistente acepta en nombre de Marco solo los tipos de encargo que él define; los demás se derivan. |
| Después de aceptar un encargo | El asistente avisa a Marco y sigue respondiendo el chat, sin pausa. |
| Dónde se definen los encargos aceptables | En una property, un tipo por línea; si falta o está vacía, no se acepta ningún encargo (decidido por defecto). |
| Aviso de encargo | Qué hay que hacer, quién lo pide (nombre y número) y el enlace `wa.me`, sin resumen del LLM (decidido por defecto). |
| Derivación y encargo en el mismo turno | Un solo aviso con ambos, y la pausa de la derivación (decidido por defecto). |
| Plazos | El asistente no promete plazos al aceptar un encargo (decidido por defecto). |
| Identificar a la familia | Una lista de números en una property; un número que no está en la lista recibe trato profesional. |
| Trato familiar | Instrucciones de tono propias en una property, que se suman al prompt solo en sus chats, y cualquier encargo aceptable sin la lista. |
| Trato del resto | Profesional, según el prompt del «Agente WhatsApp» (dato, no código). |
| Comparación de números | Solo los dígitos, sin espacios ni signos (decidido por defecto). |
| Seguridad con la familia | Las reglas de seguridad del prompt valen igual; un encargo de dinero se acepta como encargo y llega en el aviso (decidido por defecto). |
| Aviso de un familiar | Indica que el contacto es del círculo familiar (decidido por defecto). |

## Requisitos funcionales

"El sistema" es la API del asistente; "contacto", "Marco", "lote" y "ventana de pausa" son los de la spec 006;
"derivar" es la acción del coordinador de WhatsApp que pide que Marco atienda la conversación; "encargo" es algo que el
contacto le pide hacer a Marco y que el coordinador registra con su descripción; "aviso" es el mensaje que se publica en
Google Chat por una derivación, por encargos o por ambos; "space de aviso" es el space de Google Chat configurado como
destino; "encargos aceptables" es la lista de tipos de encargo que Marco permite aceptar; "familiar" es un contacto cuyo número
está en la lista de números familiares; "instrucciones familiares" son las indicaciones de trato para esos contactos.

### Derivación

- **RF-01** — El sistema debe ofrecer al coordinador de WhatsApp, además de sus herramientas de consulta, una acción
  para derivar la conversación a Marco indicando un motivo.
- **RF-02** — El sistema no debe ofrecer la acción de derivar a los coordinadores del chat web ni de Google Chat.
- **RF-03** — Cuando el coordinador derive en un turno, el sistema debe enviar al contacto la respuesta de ese turno
  igual que cualquier otra respuesta del canal.
- **RF-04** — Cuando el coordinador derive en un turno, el sistema debe publicar un aviso en el space de aviso.
- **RF-05** — Cuando el aviso se publique, el sistema debe pausar la conversación hasta que venza la ventana de pausa,
  contada desde la publicación del aviso.
- **RF-06** — Si el coordinador deriva más de una vez en el mismo turno, entonces el sistema debe publicar un solo aviso
  con todos los motivos.
- **RF-07** — Si la respuesta del turno se descarta porque Marco escribió durante el lote, entonces el sistema no debe
  publicar el aviso.
- **RF-08** — Si YCloud rechaza la respuesta de un turno en que se derivó o se registró un encargo, entonces el sistema
  debe publicar igual el aviso.
- **RF-09** — El sistema no debe publicar un aviso cuando la conversación se pausa porque Marco respondió desde la app.

### Contenido del aviso

- **RF-10** — El sistema debe publicar el aviso en español con el nombre de perfil de WhatsApp del contacto (o «Sin
  nombre» si no lo informa), su número, el o los motivos y el enlace `https://wa.me/<número sin el signo +>`.
- **RF-11** — El sistema debe incluir en el aviso un resumen breve en español de la conversación, redactado por el LLM
  a partir del historial de la conversación y del lote en curso.
- **RF-12** — Si el LLM falla o supera el tiempo máximo al redactar el resumen, entonces el sistema debe publicar el
  aviso sin resumen, con una línea que indique que no se pudo resumir.

### Fallos del aviso

- **RF-13** — Si la publicación del aviso falla, entonces el sistema debe reintentarla hasta 2 veces más.
- **RF-14** — Si el aviso no se publica después de los reintentos, o el space de aviso no está configurado, entonces el
  sistema no debe pausar la conversación y debe registrar el error en el log sin el contenido de la conversación.

### Configuración

- **RF-15** — El sistema debe leer el space de aviso desde la tabla `property` en cada derivación, de modo que un cambio
  aplique desde la siguiente.

### Conexiones salientes

- **RF-16** — El sistema debe establecer las conexiones a YCloud y a la API de Google Chat solo por IPv4.
- **RF-17** — Si no se logra establecer la conexión con YCloud o con la API de Google Chat, entonces el sistema debe
  reintentar la conexión hasta 2 veces antes de tratar la petición como fallida.
- **RF-18** — El sistema no debe repetir una petición a YCloud o a la API de Google Chat que ya se envió, para no
  duplicar mensajes.

### Encargos

- **RF-19** — El sistema debe ofrecer al coordinador de WhatsApp una acción para registrar un encargo con la descripción
  de lo que Marco tiene que hacer.
- **RF-20** — El sistema debe entregar al coordinador de WhatsApp, en cada turno, los encargos aceptables leídos desde la
  tabla `property`, de modo que un cambio aplique desde el siguiente turno.
- **RF-21** — Si los encargos aceptables no están configurados o están vacíos, entonces el sistema debe indicar al
  coordinador que no acepte encargos y que los derive.
- **RF-22** — Cuando el coordinador registre un encargo, el sistema debe publicar en el space de aviso un aviso en
  español con la descripción del encargo, el nombre de perfil del contacto (o «Sin nombre»), su número y el enlace
  `https://wa.me/<número sin el signo +>`.
- **RF-23** — Cuando el coordinador registre un encargo sin derivar en el mismo turno, el sistema no debe pausar la
  conversación.
- **RF-24** — Si el coordinador registra varios encargos en el mismo turno, entonces el sistema debe publicar un solo
  aviso con todos.
- **RF-25** — Si en el mismo turno el coordinador deriva y registra encargos, entonces el sistema debe publicar un solo
  aviso con los motivos, el resumen y los encargos, y pausar la conversación según RF-05.
- **RF-26** — Si el aviso de un turno con encargos y sin derivación no se publica después de los reintentos, entonces el
  sistema debe registrar el error en el log con el número del contacto y sin la descripción del encargo.

### Círculo familiar

- **RF-27** — El sistema debe considerar familiar a un contacto cuyo número coincida, comparando solo los dígitos, con
  uno de la lista de números familiares.
- **RF-28** — Mientras el contacto sea familiar, el sistema debe entregar al coordinador de WhatsApp las instrucciones
  familiares en cada turno.
- **RF-29** — Mientras el contacto sea familiar, el sistema debe indicar al coordinador que puede aceptar cualquier
  encargo, en lugar de los encargos aceptables.
- **RF-30** — Mientras el contacto no sea familiar, el sistema no debe entregar las instrucciones familiares al
  coordinador.
- **RF-31** — El sistema debe leer la lista de números familiares y las instrucciones familiares desde la tabla
  `property` en cada turno, de modo que un cambio aplique desde el siguiente.
- **RF-32** — Si la lista de números familiares no está configurada o está vacía, entonces el sistema debe tratar a
  todos los contactos como no familiares.
- **RF-33** — Cuando el aviso sea de un contacto familiar, el sistema debe indicarlo en el aviso.

## Fuera de alcance

- Avisar cuando la pausa se activa porque Marco respondió desde el celular: descartado (RF-09).
- Pausar igual cuando el aviso falla, o reintentarlo en segundo plano durante minutos: descartados (RF-14).
- Avisos con los últimos mensajes textuales o solo con lo mínimo: descartados; el aviso lleva un resumen (RF-11).
- Seguir respondiendo, o pausar indefinidamente, después de derivar: descartados (RF-05).
- Pausar la conversación después de aceptar un encargo: descartado (RF-23).
- Aceptar cualquier encargo, o prometer plazos: descartados; solo los tipos que Marco define y sin plazos.
- Guardar los encargos en una lista de pendientes o marcarlos como hechos: el aviso en Google Chat es el registro.
- Identificar a la familia por el nombre de perfil: descartado, porque lo elige el contacto y se puede imitar.
- Un coordinador aparte para la familia, o instrucciones por persona: descartados; hay un solo juego de instrucciones
  familiares.
- Responder al contacto o reactivar el asistente desde Google Chat: fase 3.
- Escalar consultas sin respuesta y crear FAQs desde Google Chat: fases 3 y 4.
- Las conexiones de Gemini, que van por su propio SDK.
- Cambiar la configuración de red o de DNS del cluster.

## Restricciones

- **Cambia RF-16 de la spec 006:** el coordinador de WhatsApp deja de tener solo herramientas de consulta. La nueva
  acción solo pide la derivación; no modifica datos de negocio ni sistemas externos más allá del aviso y la pausa.
- **Aprobación de esquema pendiente:** el aviso necesita el nombre de perfil del contacto, que hoy no se guarda; el plan
  definirá si hace falta una columna nueva en el esquema del canal y su migración.
- **Datos, no esquema:** el space de aviso, los encargos aceptables, los números familiares y las instrucciones
  familiares son properties nuevas de `property`, y los criterios
  para derivar y aceptar encargos se agregan al prompt del «Agente WhatsApp» en `agents`, que hoy prohíbe aceptar
  encargos. El README documenta todo con su SQL.
- **Google Chat:** la app de Chat debe poder publicar en el space de aviso (Marco debe tener un mensaje directo con la
  app); usa la cuenta de servicio y el scope `chat.bot` que ya existen.
- **Sin dependencias nuevas** previstas.
- Constitución: §4 (tests sin BD real, sin LLM, sin YCloud y sin Google), §6 (configuración en `property`), §7 (sin
  trazas al contacto), §9 (versión al cerrar la spec).

## Criterios de finalización

- [ ] Cada RF tiene al menos un test en `tests/` que pasa con `uv run pytest`, sin BD real, sin LLM y sin llamadas reales
      a YCloud ni a Google.
- [ ] `uv run pyright` termina con 0 errores, `uv run alembic check` no reporta diferencias y las migraciones quedan
      aplicadas si el plan aprobado cambia el esquema.
- [ ] README documenta la property del space de aviso, cómo obtenerlo, los criterios de derivación del prompt y el fix
      de red.
- [ ] Versión minor subida en `pyproject.toml` y `uv sync` ejecutado, en un solo commit con `uv.lock`.
- [ ] Verificación manual: desde otro teléfono, pedir una reunión recibe una respuesta del tipo «déjame revisarlo y te
      escribo»; llega a Google Chat un aviso con nombre, número, motivo, resumen y enlace `wa.me`, y el siguiente
      mensaje del contacto no recibe respuesta; pedir un encargo de un tipo aceptable recibe una respuesta del tipo «dale,
      lo hago y te aviso», llega un aviso con qué hacer y quién lo pide, y el asistente sigue respondiendo; un encargo de
      un tipo no aceptable se deriva; un número de la lista familiar recibe el tono familiar y puede pedir un encargo
      fuera de la lista, y el aviso lo marca como familia; con la property del space vacía no llega aviso y el asistente sigue respondiendo.
