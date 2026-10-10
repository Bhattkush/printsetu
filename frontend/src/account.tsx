import React, { useState } from 'react';
import { ArrowRight, Mail, Printer } from 'lucide-react';

export function Account({ request, register = false }: { request: Function; register?: boolean }) {
  const [step, setStep] = useState(register ? 'register' : 'login');
  const [form, setForm] = useState({ name: '', shop_name: '', email: '', password: '', code: '' });
  const [error, setError] = useState(''), [message, setMessage] = useState(''), [developmentCode, setDevelopmentCode] = useState('');
  const [busy, setBusy] = useState(false), [cooldown, setCooldown] = useState(0);
  React.useEffect(() => { if(!cooldown) return; const timer = setTimeout(() => setCooldown(cooldown-1),1000); return () => clearTimeout(timer); }, [cooldown]);
  const field = (key: string, value: string) => setForm(current => ({...current, [key]:value}));
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setMessage('');
    try {
      const path = step === 'verify' ? 'verify' : step === 'reset' ? 'reset-password' : step === 'forgot' ? 'forgot-password' : step;
      const result = await request('/auth/' + path, {method:'POST',body:JSON.stringify(form)});
      if(step === 'login' || step === 'verify') location.href = '/owner';
      else if(step === 'register' || step === 'forgot') { setDevelopmentCode(result.development_code || ''); setMessage(result.message); setStep(step === 'register' ? 'verify' : 'reset'); setCooldown(60); }
      else { setStep('login'); setMessage(result.message); }
    } catch(e: any) { setError(e.message); } finally {setBusy(false);}
  }
  async function resend() {
    setBusy(true); setError('');
    try { const result = await request('/auth/' + (step === 'reset' ? 'forgot-password' : 'resend'),{method:'POST',body:JSON.stringify({email:form.email})}); setDevelopmentCode(result.development_code || ''); setMessage(result.message); setCooldown(60); }
    catch(e: any) {setError(e.message);} finally {setBusy(false);}
  }
  const titles = {login:'Welcome back',register:'Create your shop',verify:'Verify your email',forgot:'Reset your password',reset:'Set a new password'};
  return <div className="account-page"><a className="brand" href="/"><span className="brand-mark"><Printer size={22}/></span>PrintSetu</a><div className="account-form"><span className="eyebrow">SHOP WORKSPACE</span><h1>{titles[step]}</h1><p>{step === 'verify' ? form.email : 'One workspace for your shop and printers.'}</p>{error && <div className="error" role="alert">{error}</div>}{message && <p role="status">{message}</p>}{developmentCode && <div className="development-code">DEVELOPMENT ONLY · Email code: <strong>{developmentCode}</strong></div>}<form onSubmit={submit}>
    {step === 'register' && <><label>Your name<input required value={form.name} onChange={e => field('name',e.target.value)} autoComplete="name"/></label><label>Shop name<input required value={form.shop_name} onChange={e => field('shop_name',e.target.value)}/></label></>}
    {!['verify','reset'].includes(step) && <label>Email address<input type="email" required value={form.email} onChange={e => field('email',e.target.value)} autoComplete="email"/></label>}
    {['verify','reset'].includes(step) && <label>Email verification code<input required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={form.code} onChange={e => field('code',e.target.value)} autoComplete="one-time-code"/></label>}
    {['login','register','reset'].includes(step) && <label>Password<input type="password" required minLength={10} maxLength={128} value={form.password} onChange={e => field('password',e.target.value)} autoComplete={step === 'login' ? 'current-password' : 'new-password'}/></label>}
    <button className="wide" disabled={busy}>{busy ? 'Please wait...' : step === 'register' ? 'Create shop' : step === 'verify' ? 'Verify email' : step === 'forgot' ? 'Send reset code' : step === 'reset' ? 'Reset password' : 'Sign in'}<ArrowRight size={17}/></button></form>
    {['verify','reset'].includes(step) && <button className="quiet" disabled={busy || cooldown > 0} onClick={resend}><Mail size={16}/>{cooldown ? 'Resend in ' + cooldown + 's' : 'Resend code'}</button>}
    {step === 'login' && <div className="account-links"><button className="quiet" onClick={() => setStep('forgot')}>Forgot password?</button><a href="/register">Register shop</a></div>}
    {step !== 'login' && <button className="quiet" onClick={() => {setStep('login');setDevelopmentCode('');}}>Back to sign in</button>}
    </div></div>;
}
