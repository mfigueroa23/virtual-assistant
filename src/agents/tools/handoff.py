from langchain.tools import tool

# Las tools solo dejan constancia en el turno: el aviso a Marco lo publica el canal después de enviar la respuesta (D2).
# Las guías dan el criterio y nunca una frase: el modelo copiaría la frase en cada chat (D11 del plan 008).
GUIA_DERIVACION = (
  "Marco recibirá el aviso y retomará la conversación. Responde al contacto con tus propias palabras, según lo que "
  "pidió y el tono de la conversación, sin prometer plazos ni confirmar lo que Marco debe decidir."
)
GUIA_ENCARGO = (
  "Marco recibirá el encargo. Confírmale al contacto con tus propias palabras que se hará, según lo que pidió y el "
  "tono de la conversación, sin prometer plazos."
)
GUIA_CONSULTA = (
  "Marco recibirá la pregunta y la responderá. Dile al contacto con tus propias palabras que lo vas a confirmar, "
  "según lo que preguntó y el tono de la conversación, sin prometer plazos ni inventar la respuesta. Sigue atendiendo "
  "el resto de la conversación."
)

@tool(parse_docstring=True)
def derivar_a_marco(motivo: str) -> str:
  """Pide que Marco atienda personalmente la conversación de WhatsApp.

  Úsala cuando el contacto quiera agendar una reunión o una llamada, pregunte por precios o condiciones, tenga una
  urgencia, insista en hablar con Marco o pida un encargo que no está entre los que puedes aceptar. Lo que no sabes
  responder se consulta con consultar_a_marco, sin derivar.

  Args:
      motivo: Por qué Marco debe atender la conversación, en una frase en español y sin datos personales.
  """
  return GUIA_DERIVACION

@tool(parse_docstring=True)
def registrar_encargo(descripcion: str) -> str:
  """Registra algo que el contacto le pide hacer a Marco, para que Marco lo haga.

  Úsala solo con encargos de los tipos que puedes aceptar según tus instrucciones; cualquier otro encargo se deriva
  con derivar_a_marco.

  Args:
      descripcion: Qué tiene que hacer Marco, en una frase en español.
  """
  return GUIA_ENCARGO

@tool(parse_docstring=True)
def consultar_a_marco(pregunta: str) -> str:
  """Consulta a Marco una pregunta que no sabes responder con la información disponible.

  Úsala cuando el contacto pregunte algo concreto que no está en la información de las áreas, como una experiencia o
  una disponibilidad que Marco puede confirmar. La conversación sigue: no la uses para reuniones, precios, urgencias
  ni cuando insista en hablar con Marco, que se derivan con derivar_a_marco.

  Args:
      pregunta: La pregunta del contacto, completa y autocontenida, en español y sin datos personales.
  """
  return GUIA_CONSULTA
