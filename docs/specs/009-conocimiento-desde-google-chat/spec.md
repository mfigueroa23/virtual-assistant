# Spec 009 — Agente Interno y aprendizaje de preguntas frecuentes desde Google Chat

- **Status:** Approved
- **Date:** 2026-10-21
- **Scope:** virtual-assistant — Google Chat (specs 001 y 003), canal WhatsApp (specs 006 a 008) y preguntas frecuentes
  de las áreas `externo`

## Por qué

Desde la spec 008, cuando el asistente de WhatsApp no sabe algo se lo consulta a Marco, y Marco responde en el hilo
del aviso en Google Chat. Pero esa respuesta solo le llega al contacto: no queda como conocimiento, así que la próxima
persona que pregunte lo mismo genera otra consulta y Marco tiene que volver a contestarla. La única forma de agregar
o corregir preguntas frecuentes es escribir SQL contra la base y ejecutar la reindexación a mano.

Además, lo que Marco escribe en Google Chat fuera de los hilos de avisos lo atiende el «Agente Interno», que en
producción no existe ni tiene áreas `interno`, así que responde «Servicio no disponible». Marco quiere que ese agente
exista y que tenga un sub-agente que administre el conocimiento que usa el «Agente WhatsApp».

## Decisiones

| Tema | Decisión |
|---|---|
| Origen de las FAQs aprendidas | Tras enviar al contacto la respuesta de Marco a una consulta, el sistema propone en el mismo hilo una FAQ redactada; solo se guarda si Marco la confirma. |
| Dónde queda la FAQ | En un área `externo` existente, la que corresponda. La usan por igual el «Agente WhatsApp» y el chat web del portafolio; sin conocimiento exclusivo de WhatsApp. |
| Qué administra el sub-agente de conocimiento | Buscar, crear, editar y borrar FAQs, y crear categorías dentro de las áreas `externo` existentes. No crea ni borra áreas ni toca prompts. |
| Confirmación de los cambios | Siempre: nada se escribe sin una confirmación explícita de Marco. |
| Acceso | Solo en el space de aviso (`whatsapp_notify_space`, el mensaje directo de Marco con la app) se puede ver y cambiar el conocimiento. En otros spaces el «Agente Interno» responde, pero sin el sub-agente de conocimiento. |
| Cómo se confirma la propuesta del hilo | Con los botones «Guardar» y «Descartar» de una tarjeta, porque lo que Marco escribe en el hilo se envía al contacto. Corregir la propuesta se le pide al «Agente Interno» fuera del hilo. |
| Cómo se confirman los cambios pedidos al «Agente Interno» | También con una tarjeta con «Confirmar» y «Cancelar», en vez de esperar un «sí» escrito: la confirmación no depende de cómo interprete el modelo el mensaje siguiente (decidido por defecto). |
| Corregir una propuesta | La tarjeta no se edita: Marco la descarta y le pide al «Agente Interno» la FAQ con sus palabras (decidido por defecto). |
| Indexación | El embedding se genera al confirmar, así que la FAQ se encuentra desde la siguiente consulta sin ejecutar la reindexación (decidido por defecto). |
| Área y categoría de la propuesta | Las elige el LLM entre las áreas `externo` existentes; la categoría puede ser una existente o una nueva de esa área (decidido por defecto). |
| Varias respuestas en un hilo | Cada respuesta enviada puede generar su propuesta. No se propone si la respuesta no es conocimiento reutilizable o si ya hay una FAQ que responde la pregunta (decidido por defecto). |
| Avisos sin consulta | Las respuestas en hilos de derivaciones o encargos no generan propuestas (decidido por defecto). |

## Requisitos funcionales

"El sistema" es la API del asistente; "Marco", "contacto", "aviso", "space de aviso", "consulta", "hilo del aviso" y
"respuesta desde el hilo" son los de las specs 006 a 008. "Agente Interno" es el coordinador que atiende Google Chat
fuera de los hilos de avisos (spec 001). "Conocimiento" son las áreas de audiencia `externo` con sus categorías y
preguntas frecuentes (FAQs). "Sub-agente de conocimiento" es el sub-agente del «Agente Interno» que lo administra.
"Tarjeta" es un mensaje de Google Chat con botones; "cambio" es crear, editar o borrar una FAQ, o crear una
categoría.

### Agente Interno y sub-agente de conocimiento

- **RF-01** — El sistema debe ofrecer al «Agente Interno» un sub-agente de conocimiento que trabaja sobre las áreas
  `externo`.
- **RF-02** — El sistema no debe ofrecer las acciones del sub-agente de conocimiento a los coordinadores del chat web
  ni de WhatsApp, ni a los demás sub-agentes.
- **RF-03** — Mientras el mensaje de Google Chat no venga del space de aviso, el sistema no debe ofrecer el sub-agente
  de conocimiento al «Agente Interno».
- **RF-04** — Donde `whatsapp_notify_space` no esté configurado, el sistema no debe ofrecer el sub-agente de
  conocimiento en ningún space.

### Ver el conocimiento

- **RF-05** — Cuando Marco pida ver el conocimiento, el sub-agente debe poder listar las áreas `externo` con sus
  categorías y la cantidad de FAQs de cada una.
- **RF-06** — Cuando Marco pregunte por un tema, el sub-agente debe poder buscar FAQs de las áreas `externo` por
  similitud y mostrar el área, la categoría, la pregunta y la respuesta de cada una.

### Cambios pedidos al Agente Interno

- **RF-07** — Cuando Marco pida un cambio, el sistema debe responder con una tarjeta que muestre el área, la categoría,
  la pregunta y la respuesta resultantes, con los botones «Confirmar» y «Cancelar», sin escribir nada.
- **RF-08** — Cuando el cambio sea una edición, la tarjeta debe mostrar también la versión actual de la FAQ.
- **RF-09** — Cuando el cambio sea un borrado, la tarjeta debe mostrar la FAQ que se borrará.
- **RF-10** — Si Marco pide un cambio en un área que no existe o que no es `externo`, entonces el sistema no debe
  mostrar una tarjeta y debe responder con las áreas `externo` disponibles.
- **RF-11** — Si la respuesta del «Agente Interno» se publica después como mensaje nuevo (spec 001), entonces ese
  mensaje debe llevar la misma tarjeta con sus botones.

### Confirmar o cancelar

- **RF-12** — Cuando Marco presione «Confirmar» o «Guardar», el sistema debe aplicar el cambio y actualizar la tarjeta
  con el resultado, sin botones.
- **RF-13** — Cuando Marco presione «Cancelar» o «Descartar», el sistema no debe cambiar el conocimiento y debe
  actualizar la tarjeta indicando que se descartó, sin botones.
- **RF-14** — Cuando se confirme crear o editar una FAQ, el sistema debe generar su embedding en ese momento, de modo
  que los sub-agentes la encuentren desde la siguiente consulta sin ejecutar la reindexación.
- **RF-15** — Si no se puede generar el embedding, entonces el sistema no debe aplicar el cambio y debe indicarlo en la
  tarjeta, conservando los botones para reintentar.
- **RF-16** — Si al confirmar la creación ya existe una FAQ con la misma pregunta en esa categoría, entonces el sistema
  no debe aplicar el cambio y debe indicarlo en la tarjeta, sin botones.
- **RF-17** — Si al confirmar una edición o un borrado la FAQ ya no existe o cambió desde que se mostró la tarjeta,
  entonces el sistema no debe aplicar el cambio y debe indicarlo en la tarjeta, sin botones.
- **RF-18** — Si se presiona un botón de una tarjeta que ya se confirmó o descartó, entonces el sistema no debe
  aplicar nada y debe mostrar el estado en que quedó.
- **RF-19** — Si se presiona un botón de una tarjeta fuera del space de aviso, entonces el sistema no debe aplicar
  nada y debe responder que el conocimiento solo se administra desde el space de aviso.

### Proponer una FAQ desde una consulta

- **RF-20** — Cuando Marco responda en el hilo de un aviso que incluía preguntas y la respuesta se envíe al contacto,
  el sistema debe redactar con el LLM una FAQ propuesta (área `externo`, categoría, pregunta y respuesta) a partir de
  las preguntas del aviso y de la respuesta de Marco.
- **RF-21** — Cuando haya una FAQ propuesta, el sistema debe publicarla en el hilo del aviso como tarjeta con los
  botones «Guardar» y «Descartar».
- **RF-22** — La FAQ propuesta no debe incluir el nombre, el número ni otros datos personales del contacto.
- **RF-23** — Si el LLM juzga que la respuesta de Marco no es conocimiento reutilizable (es personal, puntual o solo
  vale para ese contacto), entonces el sistema no debe publicar una propuesta.
- **RF-24** — Si ya existe una FAQ que responde la pregunta, entonces el sistema no debe publicar una propuesta.
- **RF-25** — Si el aviso solo tenía derivaciones o encargos, entonces el sistema no debe proponer una FAQ.
- **RF-26** — Si redactar o publicar la propuesta falla, entonces el mensaje al contacto y la confirmación del envío
  en el hilo deben quedar como en la spec 008.

### Registro

- **RF-27** — El sistema no debe registrar en el log el texto de las preguntas ni de las respuestas cuando falle una
  propuesta o un cambio.

## Fuera de alcance

- Crear, renombrar o borrar áreas, editar prompts de agentes, y renombrar o borrar categorías.
- Conocimiento exclusivo de WhatsApp o elegir en cada FAQ si es pública: descartado; todo queda en las áreas `externo`
  compartidas con el chat web.
- Guardar las FAQs sin confirmación, o solo a pedido sin propuesta en el hilo: descartado; se propone en el hilo y se
  confirma.
- Confirmar escribiendo «sí», con un mensaje aparte fuera del hilo o con un prefijo dentro del hilo: descartado; se
  confirma con botones.
- Permitir cambios desde otros spaces, por una lista de correos o a cualquiera: descartado; solo el space de aviso.
- Editar la propuesta dentro de la tarjeta.
- Proponer FAQs a partir de derivaciones, encargos o de lo que Marco responde desde el celular.
- Otras áreas `interno` además del conocimiento: se pueden cargar después como datos, sin cambios de código.
- Carga masiva de FAQs: sigue la reindexación de `src.rag.reindex`.
- Corregir la descripción desactualizada de `search_faq` («consulta sobre Autofin»): se registra aparte.

## Restricciones

- **Aprobación de esquema pendiente:** confirmar un cambio exige recordar qué propone cada tarjeta entre que se
  muestra y que Marco presiona un botón, también después de un reinicio, y saber si ya se confirmó o descartó (RF-18).
  El plan decidirá si basta con los parámetros de la tarjeta o si hace falta una tabla y su migración.
- **Cambia la spec 001:** el endpoint de Google Chat recibe además los clics en los botones de las tarjetas, y las
  respuestas del «Agente Interno», incluidas las diferidas, pueden llevar una tarjeta.
- **Cambia la spec 008:** después de enviar una respuesta desde el hilo de una consulta, el hilo puede recibir además
  la tarjeta con la propuesta.
- **Google Chat:** el diseño depende de que el complemento reciba los clics de los botones en el mismo endpoint y con
  el mismo token, y de que pueda actualizar la tarjeta presionada. Se comprueba al inicio del plan.
- **Datos:** el «Agente Interno», su prompt, el área `interno` del conocimiento y su sub-agente se cargan en la base.
  El README documenta los `INSERT` y Marco los revisa antes de aplicarlos en producción.
- **Sin dependencias nuevas** previstas.
- Constitución: §2 (los cambios de conocimiento van en `services`), §4 (tests sin BD real, sin LLM y sin Google), §6
  (configuración en `property`), §7 (sin trazas ni detalles internos en las respuestas), §9 (versión al cerrar la spec).

## Criterios de finalización

- [ ] Cada RF tiene al menos un test en `tests/` que pasa con `uv run pytest`, sin BD real, sin LLM y sin llamadas reales
      a YCloud ni a Google.
- [ ] `uv run pyright` termina con 0 errores, `uv run alembic check` no reporta diferencias y las migraciones del
      esquema aprobado, si las hay, quedan aplicadas.
- [ ] README documenta el «Agente Interno», el sub-agente de conocimiento, quién puede usarlo, las tarjetas de
      confirmación, las propuestas desde el hilo y los `INSERT` para cargarlos.
- [ ] Versión minor subida en `pyproject.toml` y `uv sync` ejecutado, en un solo commit con `uv.lock`.
- [ ] Verificación manual:
  - un mensaje en el space de aviso fuera de los hilos lo responde el «Agente Interno» y no «Servicio no disponible»;
  - pedirle que liste el conocimiento muestra las áreas `externo` con sus categorías;
  - pedirle crear una FAQ muestra una tarjeta; «Confirmar» la guarda y el «Agente WhatsApp» la usa en la siguiente
    pregunta desde otro teléfono;
  - editar y borrar una FAQ piden confirmación y «Cancelar» no cambia nada;
  - responder en el hilo de una consulta envía la respuesta al contacto y publica una propuesta; «Guardar» la deja
    disponible para la siguiente pregunta igual;
  - en otro space, el «Agente Interno» no puede ver ni cambiar el conocimiento.
