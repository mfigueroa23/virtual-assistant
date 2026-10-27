# Spec 011 — Audiencia personal para WhatsApp y administración de todo el conocimiento

- **Status:** Approved
- **Date:** 2026-10-26
- **Scope:** virtual-assistant — canal WhatsApp (specs 006 a 008), «Agente Interno» y sub-agente de conocimiento
  (spec 009), áreas y sub-agentes de las tres audiencias

## Por qué

El «Agente WhatsApp» usa las mismas áreas `externo` que el chat web del portafolio. Por eso todo lo que Marco quiera
que el asistente sepa para atender su WhatsApp termina siendo público en su página. Las FAQs que se aprenden de las
consultas por WhatsApp (spec 009) caen en un área `externo` y también las ve cualquier visitante del chat web. No hay
un lugar para el conocimiento que solo debe usarse en WhatsApp.

Además, el sub-agente de conocimiento de Google Chat solo administra las áreas `externo`. Para cambiar el conocimiento
del «Agente Interno», o para crear un área nueva con su sub-agente, hay que escribir SQL contra la base. Marco quiere
administrar desde Google Chat el conocimiento de las tres audiencias, incluida la nueva audiencia `personal` de
WhatsApp.

## Decisiones

| Tema | Decisión |
|---|---|
| Áreas que ve WhatsApp | Las `personal` y las `externo`: sigue respondiendo lo profesional sin duplicar FAQs, y lo personal no llega al chat web. |
| Quién administra el conocimiento | El sub-agente «Conocimiento» del «Agente Interno», ampliado a las tres audiencias, con las mismas tarjetas y el mismo límite al space de aviso de la spec 009. Los contactos de WhatsApp nunca cambian el conocimiento. |
| Dónde quedan las FAQs aprendidas de WhatsApp | Solo en áreas `personal`. Para publicar algo en el portafolio, Marco se lo pide a «Conocimiento» aparte. |
| Quién ve las áreas `personal` | Todos los contactos de WhatsApp por igual, sean o no familia. Marco solo guarda ahí lo que acepta que sepa cualquiera que le escriba. |
| Crear áreas | «Conocimiento» crea áreas de cualquier audiencia con su sub-agente, confirmando con tarjeta. |
| Prompt del sub-agente nuevo | Un prompt base por audiencia, fijo en el sistema y con el nombre y la descripción del área. Editarlo después queda fuera de alcance (decidido por defecto). |
| Nombre del sub-agente nuevo | «Agente \<nombre del área\>», como los sub-agentes actuales (decidido por defecto). |
| Nombres de áreas | Únicos entre las tres audiencias, como hoy (decidido por defecto). |
| FAQs aprendidas antes de esta spec | Se quedan en las áreas `externo` donde están; moverlas es un cambio que Marco pide a «Conocimiento» (decidido por defecto). |
| Sin áreas `personal` | WhatsApp sigue funcionando solo con las `externo`, y las consultas no generan propuestas (decidido por defecto). |

## Requisitos funcionales

"El sistema" es la API del asistente. "Marco", "contacto", "consulta", "hilo del aviso", "space de aviso", "tarjeta" y
"cambio" son los de las specs 006 a 009. "Audiencia" es una de `externo` (chat web y WhatsApp), `interno` (Google
Chat) o `personal` (solo WhatsApp). "Conocimiento" son las áreas de las tres audiencias con sus categorías y FAQs.
"Sub-agente de conocimiento" es el sub-agente «Conocimiento» del «Agente Interno» (spec 009).

### Audiencia personal

- **RF-01** — El sistema debe aceptar `personal` como audiencia de un área, además de `externo` e `interno`.
- **RF-02** — Cuando el «Agente WhatsApp» atienda un mensaje, el sistema debe ofrecerle los sub-agentes de las áreas
  `personal` y `externo`.
- **RF-03** — El sistema no debe ofrecer los sub-agentes de las áreas `personal` al coordinador del chat web ni al
  «Agente Interno».
- **RF-04** — El sistema no debe ofrecer los sub-agentes de las áreas `interno` al «Agente WhatsApp».
- **RF-05** — Cuando un sub-agente busque FAQs, el sistema debe devolver solo FAQs de su propia área.
- **RF-06** — El sistema debe ofrecer los sub-agentes de las áreas `personal` a todos los contactos de WhatsApp por
  igual, sean o no familia.
- **RF-07** — Si no hay áreas `personal`, entonces el «Agente WhatsApp» debe responder con los sub-agentes de las
  áreas `externo`, como antes de esta spec.

### Ver el conocimiento

- **RF-08** — Cuando Marco pida ver el conocimiento, el sub-agente de conocimiento debe listar las áreas de las tres
  audiencias, con la audiencia de cada una, sus categorías y la cantidad de FAQs de cada categoría.
- **RF-09** — Cuando Marco pregunte por un tema, el sub-agente de conocimiento debe buscar FAQs por similitud en las
  tres audiencias y mostrar la audiencia, el área, la categoría, la pregunta y la respuesta de cada una.

### Cambios en FAQs y categorías

- **RF-10** — Cuando Marco pida un cambio en un área de cualquier audiencia, el sistema debe responder con la tarjeta
  de confirmación de la spec 009 (RF-07 a RF-09), sin escribir nada.
- **RF-11** — La tarjeta de un cambio debe mostrar la audiencia del área, además de lo que pide la spec 009.
- **RF-12** — Si Marco pide un cambio en un área que no existe, entonces el sistema no debe mostrar una tarjeta y debe
  responder con las áreas disponibles agrupadas por audiencia.
- **RF-13** — Cuando Marco confirme o cancele un cambio en un área de cualquier audiencia, el sistema debe aplicarlo o
  descartarlo con las reglas de la spec 009 (RF-12 a RF-19).

### Crear áreas

- **RF-14** — Cuando Marco pida crear un área, el sistema debe responder con una tarjeta que muestre el nombre, la
  audiencia y la descripción del área, con los botones «Confirmar» y «Cancelar», sin crear nada.
- **RF-15** — Cuando Marco confirme la creación de un área, el sistema debe crear el área y actualizar la tarjeta con
  el resultado, sin botones.
- **RF-16** — Cuando el sistema cree un área, debe crear también su sub-agente «Agente \<nombre del área\>» con el
  prompt base de su audiencia, que incluye el nombre y la descripción del área.
- **RF-17** — Cuando se cree un área, el sistema debe ofrecer su sub-agente a los coordinadores de su audiencia desde
  el siguiente mensaje, sin reiniciar.
- **RF-18** — Cuando Marco cancele la creación de un área, el sistema no debe crear nada y debe actualizar la tarjeta
  indicando que se descartó, sin botones.
- **RF-19** — Si Marco pide crear un área con un nombre que ya existe en cualquier audiencia, entonces el sistema no
  debe mostrar una tarjeta y debe responder que el área ya existe y en qué audiencia.
- **RF-20** — Si al confirmar ya existe un área con ese nombre, entonces el sistema no debe crear nada y debe
  indicarlo en la tarjeta, sin botones.
- **RF-21** — Si Marco pide crear un área sin nombre, sin descripción o con una audiencia que no es `externo`,
  `interno` ni `personal`, entonces el sistema no debe mostrar una tarjeta y debe responder qué falta o cuáles son las
  audiencias válidas.
- **RF-22** — Si se presiona un botón de una tarjeta de creación que ya se confirmó o descartó, o fuera del space de
  aviso, entonces el sistema no debe crear nada y debe responder como en la spec 009 (RF-18, RF-19).

### Propuestas desde consultas de WhatsApp

- **RF-23** — Cuando el sistema redacte una FAQ propuesta desde la respuesta de Marco en el hilo de una consulta (RF-20
  de la spec 009), debe ubicarla en un área `personal` existente.
- **RF-24** — Cuando el sistema busque si ya existe una FAQ que responde la pregunta (RF-24 de la spec 009), debe
  buscar en las áreas `personal` y `externo`.
- **RF-25** — La tarjeta de la propuesta debe mostrar la audiencia `personal` del área.
- **RF-26** — Si no hay áreas `personal`, entonces el sistema no debe publicar una propuesta y el mensaje al contacto
  y su confirmación en el hilo deben quedar como en la spec 008.
- **RF-27** — Si el LLM propone un área que no es `personal`, entonces el sistema no debe publicar la propuesta.

### Compatibilidad y registro

- **RF-28** — Las propuestas pendientes creadas antes de esta spec deben poder confirmarse o descartarse con las
  reglas de la spec 009.
- **RF-29** — El sistema no debe registrar en el log el texto de las preguntas, las respuestas ni las descripciones de
  áreas cuando falle una propuesta o un cambio.

## Fuera de alcance

- Renombrar o borrar áreas, cambiar la audiencia de un área existente y renombrar o borrar categorías.
- Editar desde Google Chat los prompts de los sub-agentes o de los coordinadores. El prompt base de un área nueva se
  ajusta con SQL.
- Que Marco cambie el conocimiento desde su WhatsApp, o que lo hagan los contactos: descartado. Solo «Conocimiento» en
  el space de aviso.
- Áreas `personal` solo para familia u otros grupos de contactos: descartado. Todas las ven todos los contactos.
- Guardar las FAQs aprendidas de WhatsApp en `externo`, que lo elija el LLM o que la tarjeta ofrezca publicarlas:
  descartado. Van siempre a `personal`.
- Que el «Agente WhatsApp» vea las áreas `interno`: descartado.
- Mover las FAQs aprendidas antes de esta spec a áreas `personal`.
- Mover una FAQ de un área a otra como un solo cambio: se hace creando la nueva y borrando la vieja.
- El caso de la conversación con un contacto en que el asistente le siguió la corriente con un recuerdo inventado: se
  corrige en el prompt del «Agente WhatsApp», aparte.

## Restricciones

- **Aprobación de esquema pendiente:** la audiencia `personal` hoy no la acepta la base (RF-01). Una propuesta de
  creación de área debe recordar el nombre, la audiencia y la descripción entre que se muestra la tarjeta y que Marco
  presiona un botón, también después de un reinicio (RF-14 a RF-22). El plan decidirá los cambios a los modelos y su
  migración.
- **Cambia la spec 009:** RF-01, RF-05, RF-06 y RF-10 pasan de las áreas `externo` a las tres audiencias, y RF-20
  ubica las propuestas en `personal` en vez de `externo`.
- **Cambia la spec 006:** el «Agente WhatsApp» deja de usar solo la audiencia `externo`.
- **Datos:** los prompts del «Agente WhatsApp» y de «Conocimiento» deben mencionar las áreas `personal`, y las áreas
  `personal` iniciales se pueden crear desde «Conocimiento» después del despliegue. El README documenta los `UPDATE`
  de esos prompts y Marco los revisa antes de aplicarlos en producción.
- **Sin dependencias nuevas** previstas.
- Constitución: §2 (crear áreas y sub-agentes va en `services`), §4 (tests sin BD real, sin LLM y sin Google), §5
  (esquema con aprobación y migración), §7 (sin trazas ni detalles internos en las respuestas), §9 (versión al cerrar
  la spec).

## Criterios de finalización

- [ ] Cada RF tiene al menos un test en `tests/` que pasa con `uv run pytest`, sin BD real, sin LLM y sin llamadas reales
      a YCloud ni a Google.
- [ ] `uv run pyright` termina con 0 errores, `uv run alembic check` no reporta diferencias y las migraciones del
      esquema aprobado quedan aplicadas con `uv run alembic upgrade head`.
- [ ] README documenta la audiencia `personal`, qué áreas ve cada coordinador, la creación de áreas desde
      «Conocimiento», dónde quedan las FAQs aprendidas y los `UPDATE` de los prompts.
- [ ] Versión minor subida en `pyproject.toml` y `uv sync` ejecutado, en un solo commit con `uv.lock`.
- [ ] Verificación manual:
  - en el space de aviso, pedir a «Conocimiento» un área `personal` muestra una tarjeta; «Confirmar» la crea con su
    sub-agente;
  - crear una FAQ en esa área y preguntarla por WhatsApp desde otro teléfono la responde; la misma pregunta en el chat
    web no la encuentra;
  - listar el conocimiento muestra las tres audiencias, y editar una FAQ `interno` pide confirmación y la aplica;
  - responder en el hilo de una consulta propone la FAQ en un área `personal`;
  - por WhatsApp se siguen respondiendo preguntas profesionales con las áreas `externo`.
