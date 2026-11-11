"use strict";
document.addEventListener("DOMContentLoaded",()=>{
  const form=document.getElementById("loginForm");
  const submit=document.getElementById("signIn");
  const error=document.getElementById("loginError");
  form.addEventListener("submit",async(event)=>{
    event.preventDefault();
    error.hidden=true;error.textContent="";
    submit.disabled=true;
    try{
      const response=await fetch("/api/auth/login",{
        method:"POST",
        credentials:"same-origin",
        cache:"no-store",
        headers:{"Content-Type":"application/json","Accept":"application/json"},
        body:JSON.stringify({
          username:document.getElementById("username").value,
          password:document.getElementById("password").value
        })
      });
      if(response.ok){
        document.getElementById("password").value="";
        window.location.replace("/");
        return;
      }
      if(response.status===429)throw new Error("Too many login attempts. Try again in 10 minutes.");
      if(response.status===401)throw new Error("Username or password is incorrect.");
      throw new Error("Login unavailable. Check your HP laptop server.");
    }catch(err){
      error.textContent=err.message||"Cannot connect to ZEUS.";
      error.hidden=false;
    }finally{submit.disabled=false;}
  });
});
