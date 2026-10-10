import React, { useEffect, useState } from 'react';
import { Check, Download, Printer, ShieldCheck } from 'lucide-react';

export function ShopSettings({ shop, request, onSave }: any) {
  const [form,setForm] = useState<any>(null), [details,setDetails] = useState({...shop});
  const [message,setMessage] = useState(''), [error,setError] = useState(''), [busy,setBusy] = useState(false);
  useEffect(() => { request('/owner/shops/' + shop.id + '/pricing').then(setForm).catch((e: any)=>setError(e.message)); },[shop.id]);
  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setMessage(''); setError('');
    try {await request('/owner/shops/' + shop.id + '/pricing',{method:'PATCH',body:JSON.stringify(form)}); await request('/owner/shops/' + shop.id,{method:'PATCH',body:JSON.stringify(details)}); setMessage('Settings saved'); onSave();}
    catch(e: any) {setError(e.message);} finally {setBusy(false);}
  }
  return <section className="orders-section">{error && <div className="error">{error}</div>}{form && <form onSubmit={save}><h2>Shop settings</h2><div className="settings-grid"><label>Shop name<input value={details.name} required onChange={e=>setDetails({...details,name:e.target.value})}/></label><label>Location<input value={details.location} required onChange={e=>setDetails({...details,location:e.target.value})}/></label>{[['bw_page_price','B&W / page (INR)'],['color_page_price','Color / page (INR)'],['base_price','Base price (INR)'],['minimum_order','Minimum order (INR)']].map(([key,label])=><label key={key}>{label}<input type="number" required min="0" max="10000" step="0.01" value={form[key]} onChange={e=>setForm({...form,[key]:Number(e.target.value)})}/></label>)}<label>Document retention (hours)<input type="number" min="1" max="24" value={details.retention_hours} onChange={e=>setDetails({...details,retention_hours:Number(e.target.value)})}/></label></div><button disabled={busy}><Check size={16}/>{busy ? 'Saving...' : 'Save settings'}</button>{message && <p role="status">{message}</p>}</form>}</section>;
}
export function PaymentRecords({ request }: any) {
  const [records,setRecords]=useState<any[]>([]), [error,setError]=useState('');
  const load=()=>request('/owner/payments').then(setRecords).catch((e: any)=>setError(e.message));
  useEffect(()=>{load();},[]);
  async function refund(id: string) {
    if(!window.confirm('Request a full refund? This requires a captured payment and a failed or absent print job.')) return;
    try {await request('/owner/payments/'+id+'/refund',{method:'POST'}); load();} catch(e: any){setError(e.message);}
  }
  return <section className="orders-section"><h2>Payment records</h2>{error && <div className="error">{error}</div>}{records.length ? <div className="table-scroll"><table><thead><tr><th>Order</th><th>Amount</th><th>Environment</th><th>Status</th><th>Refund</th></tr></thead><tbody>{records.map(p=><tr key={p.id}><td>{p.order_id.slice(0,8)}</td><td>INR {(p.amount_paise/100).toFixed(2)}</td><td>{p.mode === 'test' ? 'Razorpay sandbox' : p.mode}</td><td>{p.status}</td><td>{p.refunds.length ? p.refunds.map((r:any)=>r.status).join(', ') : <button className="secondary" disabled={p.status !== 'CAPTURED'} onClick={()=>refund(p.id)}>Request refund</button>}</td></tr>)}</tbody></table></div> : <div className="empty">No payment records yet</div>}</section>;
}
export function Onboarding({ devices, onConnect }: any) {
  return <section className="orders-section"><h2>Shop setup</h2><div className="setup-steps"><div><ShieldCheck size={24}/><h3>1. Account verified</h3><span>Email verification complete</span></div><div><Download size={24}/><h3>2. Install connector</h3><a className="button secondary" href="/api/agent/download">Download Windows installer</a></div><div><Printer size={24}/><h3>3. Pair and test</h3><button className="secondary" onClick={onConnect}>{devices.length ? 'Pair another computer' : 'Connect computer'}</button></div></div></section>;
}
