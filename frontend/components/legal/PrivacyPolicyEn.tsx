export function PrivacyPolicyEn() {
  return (
    <div className="space-y-4">
      <p><strong>Last Updated:</strong> May 6, 2026</p>
      <p>Welcome to <strong>webdoc.life</strong>. We take your medical privacy with the utmost seriousness. This Policy describes how we process and protect your data.</p>
      
      <h3 className="text-lg font-bold mt-6 text-slate-800">1. Data Collection and Processing</h3>
      <p>When you upload medical documents (PDFs or images) for analysis, we extract the text from them. We also store basic profile information (age, gender, biometric data) that you provide voluntarily to improve the accuracy of the AI analysis.</p>
      
      <h3 className="text-lg font-bold mt-6 text-slate-800">2. Sending the Document to AI (Important)</h3>
      <p>This demo version does <strong>not anonymize anything locally</strong>. The image of your report (PDF or photo) is sent to an external LLM provider as is, including the name and other details printed on it.</p>
      <ul className="list-disc pl-6 space-y-1">
        <li>Do not upload documents you are not ready to share with a third party.</li>
        <li>The service is a technology demonstration and is not intended for real medical documents.</li>
      </ul>

      <h3 className="text-lg font-bold mt-6 text-slate-800">3. Use of Artificial Intelligence</h3>
      <p>We use advanced LLM models to structure and interpret medical data. The AI provides reference information, which does not constitute a medical diagnosis. How the external provider handles the submitted data is governed by its own terms.</p>

      <h3 className="text-lg font-bold mt-6 text-slate-800">4. Your Rights</h3>
      <p>You have complete control over your data. In your Dashboard, you can permanently delete uploaded analyses, indicator history, or your entire profile at any time with a single click.</p>
    </div>
  );
}