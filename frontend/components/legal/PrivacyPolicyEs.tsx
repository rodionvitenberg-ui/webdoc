export function PrivacyPolicyEs() {
  return (
    <div className="space-y-4">
      <p><strong>Última actualización:</strong> 6 de mayo de 2026</p>
      <p>Bienvenido a <strong>webdoc.life</strong>. Tomamos su privacidad médica con la mayor seriedad. Esta Política describe cómo procesamos y protegemos sus datos.</p>
      
      <h3 className="text-lg font-bold mt-6 text-slate-800">1. Recopilación y procesamiento de datos</h3>
      <p>Cuando sube documentos médicos (PDF o imágenes) para su análisis, extraemos el texto de ellos. También almacenamos información básica del perfil (edad, género, datos biométricos) que usted proporciona voluntariamente para mejorar la precisión del análisis de IA.</p>
      
      <h3 className="text-lg font-bold mt-6 text-slate-800">2. Envío del documento a la IA (Importante)</h3>
      <p>Esta versión de demostración <strong>no anonimiza nada localmente</strong>. La imagen de su informe (PDF o foto) se envía a un proveedor externo de LLM tal cual, incluido el nombre y otros datos impresos en él.</p>
      <ul className="list-disc pl-6 space-y-1">
        <li>No suba documentos que no esté dispuesto a compartir con terceros.</li>
        <li>El servicio es una demostración tecnológica y no está destinado a documentos médicos reales.</li>
      </ul>

      <h3 className="text-lg font-bold mt-6 text-slate-800">3. Uso de Inteligencia Artificial</h3>
      <p>Utilizamos modelos avanzados de LLM para estructurar e interpretar datos médicos. La IA proporciona información de referencia, que no constituye un diagnóstico médico. El tratamiento de los datos enviados por el proveedor externo se rige por sus propias condiciones.</p>

      <h3 className="text-lg font-bold mt-6 text-slate-800">4. Sus derechos</h3>
      <p>Tiene control total sobre sus datos. En su Panel de control, puede eliminar permanentemente los análisis subidos, el historial de indicadores o todo su perfil en cualquier momento con un solo clic.</p>
    </div>
  );
}