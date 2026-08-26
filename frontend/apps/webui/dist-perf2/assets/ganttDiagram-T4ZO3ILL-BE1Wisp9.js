import{aZ as Te,a_ as Ve,a$ as be,b0 as we,b1 as _e,b2 as Ot,b3 as Re,aS as Yt,g as He,s as Be,D as qe,C as Ge,b as Xe,d as je,e as c,f as ft,h as bt,i as Ze,b4 as X,l as ot,p as Ue,o as Qe,I as Ke,B as Je}from"./index-6f0JZkw-.js";import{t as tr,m as er,a as rr,b as ae,c as oe,d as nr,e as ir,f as sr,g as ar,h as or,i as cr,j as lr,k as ce,l as le,n as ue,s as de,o as fe}from"./time-gNz0gwna.js";import{l as ur}from"./linear-Dd_SW17D.js";import"./init-Dmth1JHB.js";import"./defaultLocale-DX6XiGOO.js";function dr(t){return t}var _t=1,Wt=2,Ht=3,wt=4,he=1e-6;function fr(t){return"translate("+t+",0)"}function hr(t){return"translate(0,"+t+")"}function mr(t){return e=>+t(e)}function kr(t,e){return e=Math.max(0,t.bandwidth()-e*2)/2,t.round()&&(e=Math.round(e)),i=>+t(i)+e}function yr(){return!this.__axis}function De(t,e){var i=[],n=null,a=null,f=6,d=6,T=3,E=typeof window<"u"&&window.devicePixelRatio>1?0:.5,A=t===_t||t===wt?-1:1,b=t===wt||t===Wt?"x":"y",L=t===_t||t===Ht?fr:hr;function C(D){var V=n??(e.ticks?e.ticks.apply(e,i):e.domain()),I=a??(e.tickFormat?e.tickFormat.apply(e,i):dr),S=Math.max(f,0)+T,M=e.range(),W=+M[0]+E,Y=+M[M.length-1]+E,R=(e.bandwidth?kr:mr)(e.copy(),E),H=D.selection?D.selection():D,$=H.selectAll(".domain").data([null]),p=H.selectAll(".tick").data(V,e).order(),h=p.exit(),u=p.enter().append("g").attr("class","tick"),x=p.select("line"),v=p.select("text");$=$.merge($.enter().insert("path",".tick").attr("class","domain").attr("stroke","currentColor")),p=p.merge(u),x=x.merge(u.append("line").attr("stroke","currentColor").attr(b+"2",A*f)),v=v.merge(u.append("text").attr("fill","currentColor").attr(b,A*S).attr("dy",t===_t?"0em":t===Ht?"0.71em":"0.32em")),D!==H&&($=$.transition(D),p=p.transition(D),x=x.transition(D),v=v.transition(D),h=h.transition(D).attr("opacity",he).attr("transform",function(k){return isFinite(k=R(k))?L(k+E):this.getAttribute("transform")}),u.attr("opacity",he).attr("transform",function(k){var m=this.parentNode.__axis;return L((m&&isFinite(m=m(k))?m:R(k))+E)})),h.remove(),$.attr("d",t===wt||t===Wt?d?"M"+A*d+","+W+"H"+E+"V"+Y+"H"+A*d:"M"+E+","+W+"V"+Y:d?"M"+W+","+A*d+"V"+E+"H"+Y+"V"+A*d:"M"+W+","+E+"H"+Y),p.attr("opacity",1).attr("transform",function(k){return L(R(k)+E)}),x.attr(b+"2",A*f),v.attr(b,A*S).text(I),H.filter(yr).attr("fill","none").attr("font-size",10).attr("font-family","sans-serif").attr("text-anchor",t===Wt?"start":t===wt?"end":"middle"),H.each(function(){this.__axis=R})}return C.scale=function(D){return arguments.length?(e=D,C):e},C.ticks=function(){return i=Array.from(arguments),C},C.tickArguments=function(D){return arguments.length?(i=D==null?[]:Array.from(D),C):i.slice()},C.tickValues=function(D){return arguments.length?(n=D==null?null:Array.from(D),C):n&&n.slice()},C.tickFormat=function(D){return arguments.length?(a=D,C):a},C.tickSize=function(D){return arguments.length?(f=d=+D,C):f},C.tickSizeInner=function(D){return arguments.length?(f=+D,C):f},C.tickSizeOuter=function(D){return arguments.length?(d=+D,C):d},C.tickPadding=function(D){return arguments.length?(T=+D,C):T},C.offset=function(D){return arguments.length?(E=+D,C):E},C}function gr(t){return De(_t,t)}function pr(t){return De(Ht,t)}const vr=Math.PI/180,xr=180/Math.PI,$t=18,Se=.96422,Ce=1,Me=.82521,Ee=4/29,ht=6/29,Ie=3*ht*ht,Tr=ht*ht*ht;function $e(t){if(t instanceof et)return new et(t.l,t.a,t.b,t.opacity);if(t instanceof nt)return Ae(t);t instanceof Te||(t=Ve(t));var e=Vt(t.r),i=Vt(t.g),n=Vt(t.b),a=Pt((.2225045*e+.7168786*i+.0606169*n)/Ce),f,d;return e===i&&i===n?f=d=a:(f=Pt((.4360747*e+.3850649*i+.1430804*n)/Se),d=Pt((.0139322*e+.0971045*i+.7141733*n)/Me)),new et(116*a-16,500*(f-a),200*(a-d),t.opacity)}function br(t,e,i,n){return arguments.length===1?$e(t):new et(t,e,i,n??1)}function et(t,e,i,n){this.l=+t,this.a=+e,this.b=+i,this.opacity=+n}be(et,br,we(_e,{brighter(t){return new et(this.l+$t*(t??1),this.a,this.b,this.opacity)},darker(t){return new et(this.l-$t*(t??1),this.a,this.b,this.opacity)},rgb(){var t=(this.l+16)/116,e=isNaN(this.a)?t:t+this.a/500,i=isNaN(this.b)?t:t-this.b/200;return e=Se*Nt(e),t=Ce*Nt(t),i=Me*Nt(i),new Te(zt(3.1338561*e-1.6168667*t-.4906146*i),zt(-.9787684*e+1.9161415*t+.033454*i),zt(.0719453*e-.2289914*t+1.4052427*i),this.opacity)}}));function Pt(t){return t>Tr?Math.pow(t,1/3):t/Ie+Ee}function Nt(t){return t>ht?t*t*t:Ie*(t-Ee)}function zt(t){return 255*(t<=.0031308?12.92*t:1.055*Math.pow(t,1/2.4)-.055)}function Vt(t){return(t/=255)<=.04045?t/12.92:Math.pow((t+.055)/1.055,2.4)}function wr(t){if(t instanceof nt)return new nt(t.h,t.c,t.l,t.opacity);if(t instanceof et||(t=$e(t)),t.a===0&&t.b===0)return new nt(NaN,0<t.l&&t.l<100?0:NaN,t.l,t.opacity);var e=Math.atan2(t.b,t.a)*xr;return new nt(e<0?e+360:e,Math.sqrt(t.a*t.a+t.b*t.b),t.l,t.opacity)}function Bt(t,e,i,n){return arguments.length===1?wr(t):new nt(t,e,i,n??1)}function nt(t,e,i,n){this.h=+t,this.c=+e,this.l=+i,this.opacity=+n}function Ae(t){if(isNaN(t.h))return new et(t.l,0,0,t.opacity);var e=t.h*vr;return new et(t.l,Math.cos(e)*t.c,Math.sin(e)*t.c,t.opacity)}be(nt,Bt,we(_e,{brighter(t){return new nt(this.h,this.c,this.l+$t*(t??1),this.opacity)},darker(t){return new nt(this.h,this.c,this.l-$t*(t??1),this.opacity)},rgb(){return Ae(this).rgb()}}));function _r(t){return function(e,i){var n=t((e=Bt(e)).h,(i=Bt(i)).h),a=Ot(e.c,i.c),f=Ot(e.l,i.l),d=Ot(e.opacity,i.opacity);return function(T){return e.h=n(T),e.c=a(T),e.l=f(T),e.opacity=d(T),e+""}}}const Dr=_r(Re);var Dt={exports:{}},Sr=Dt.exports,me;function Cr(){return me||(me=1,(function(t,e){(function(i,n){t.exports=n()})(Sr,(function(){var i="day";return function(n,a,f){var d=function(A){return A.add(4-A.isoWeekday(),i)},T=a.prototype;T.isoWeekYear=function(){return d(this).year()},T.isoWeek=function(A){if(!this.$utils().u(A))return this.add(7*(A-this.isoWeek()),i);var b,L,C,D,V=d(this),I=(b=this.isoWeekYear(),L=this.$u,C=(L?f.utc:f)().year(b).startOf("year"),D=4-C.isoWeekday(),C.isoWeekday()>4&&(D+=7),C.add(D,i));return V.diff(I,"week")+1},T.isoWeekday=function(A){return this.$utils().u(A)?this.day()||7:this.day(this.day()%7?A:A-7)};var E=T.startOf;T.startOf=function(A,b){var L=this.$utils(),C=!!L.u(b)||b;return L.p(A)==="isoweek"?C?this.date(this.date()-(this.isoWeekday()-1)).startOf("day"):this.date(this.date()-1-(this.isoWeekday()-1)+7).endOf("day"):E.bind(this)(A,b)}}}))})(Dt)),Dt.exports}var Mr=Cr();const Er=Yt(Mr);var St={exports:{}},Ir=St.exports,ke;function $r(){return ke||(ke=1,(function(t,e){(function(i,n){t.exports=n()})(Ir,(function(){var i={LTS:"h:mm:ss A",LT:"h:mm A",L:"MM/DD/YYYY",LL:"MMMM D, YYYY",LLL:"MMMM D, YYYY h:mm A",LLLL:"dddd, MMMM D, YYYY h:mm A"},n=/(\[[^[]*\])|([-_:/.,()\s]+)|(A|a|Q|YYYY|YY?|ww?|MM?M?M?|Do|DD?|hh?|HH?|mm?|ss?|S{1,3}|z|ZZ?)/g,a=/\d/,f=/\d\d/,d=/\d\d?/,T=/\d*[^-_:/,()\s\d]+/,E={},A=function(S){return(S=+S)+(S>68?1900:2e3)},b=function(S){return function(M){this[S]=+M}},L=[/[+-]\d\d:?(\d\d)?|Z/,function(S){(this.zone||(this.zone={})).offset=(function(M){if(!M||M==="Z")return 0;var W=M.match(/([+-]|\d\d)/g),Y=60*W[1]+(+W[2]||0);return Y===0?0:W[0]==="+"?-Y:Y})(S)}],C=function(S){var M=E[S];return M&&(M.indexOf?M:M.s.concat(M.f))},D=function(S,M){var W,Y=E.meridiem;if(Y){for(var R=1;R<=24;R+=1)if(S.indexOf(Y(R,0,M))>-1){W=R>12;break}}else W=S===(M?"pm":"PM");return W},V={A:[T,function(S){this.afternoon=D(S,!1)}],a:[T,function(S){this.afternoon=D(S,!0)}],Q:[a,function(S){this.month=3*(S-1)+1}],S:[a,function(S){this.milliseconds=100*+S}],SS:[f,function(S){this.milliseconds=10*+S}],SSS:[/\d{3}/,function(S){this.milliseconds=+S}],s:[d,b("seconds")],ss:[d,b("seconds")],m:[d,b("minutes")],mm:[d,b("minutes")],H:[d,b("hours")],h:[d,b("hours")],HH:[d,b("hours")],hh:[d,b("hours")],D:[d,b("day")],DD:[f,b("day")],Do:[T,function(S){var M=E.ordinal,W=S.match(/\d+/);if(this.day=W[0],M)for(var Y=1;Y<=31;Y+=1)M(Y).replace(/\[|\]/g,"")===S&&(this.day=Y)}],w:[d,b("week")],ww:[f,b("week")],M:[d,b("month")],MM:[f,b("month")],MMM:[T,function(S){var M=C("months"),W=(C("monthsShort")||M.map((function(Y){return Y.slice(0,3)}))).indexOf(S)+1;if(W<1)throw new Error;this.month=W%12||W}],MMMM:[T,function(S){var M=C("months").indexOf(S)+1;if(M<1)throw new Error;this.month=M%12||M}],Y:[/[+-]?\d+/,b("year")],YY:[f,function(S){this.year=A(S)}],YYYY:[/\d{4}/,b("year")],Z:L,ZZ:L};function I(S){var M,W;M=S,W=E&&E.formats;for(var Y=(S=M.replace(/(\[[^\]]+])|(LTS?|l{1,4}|L{1,4})/g,(function(x,v,k){var m=k&&k.toUpperCase();return v||W[k]||i[k]||W[m].replace(/(\[[^\]]+])|(MMMM|MM|DD|dddd)/g,(function(o,l,y){return l||y.slice(1)}))}))).match(n),R=Y.length,H=0;H<R;H+=1){var $=Y[H],p=V[$],h=p&&p[0],u=p&&p[1];Y[H]=u?{regex:h,parser:u}:$.replace(/^\[|\]$/g,"")}return function(x){for(var v={},k=0,m=0;k<R;k+=1){var o=Y[k];if(typeof o=="string")m+=o.length;else{var l=o.regex,y=o.parser,g=x.slice(m),w=l.exec(g)[0];y.call(v,w),x=x.replace(w,"")}}return(function(s){var z=s.afternoon;if(z!==void 0){var r=s.hours;z?r<12&&(s.hours+=12):r===12&&(s.hours=0),delete s.afternoon}})(v),v}}return function(S,M,W){W.p.customParseFormat=!0,S&&S.parseTwoDigitYear&&(A=S.parseTwoDigitYear);var Y=M.prototype,R=Y.parse;Y.parse=function(H){var $=H.date,p=H.utc,h=H.args;this.$u=p;var u=h[1];if(typeof u=="string"){var x=h[2]===!0,v=h[3]===!0,k=x||v,m=h[2];v&&(m=h[2]),E=this.$locale(),!x&&m&&(E=W.Ls[m]),this.$d=(function(g,w,s,z){try{if(["x","X"].indexOf(w)>-1)return new Date((w==="X"?1e3:1)*g);var r=I(w)(g),_=r.year,N=r.month,P=r.day,O=r.hours,G=r.minutes,F=r.seconds,Q=r.milliseconds,it=r.zone,lt=r.week,yt=new Date,gt=P||(_||N?1:yt.getDate()),ut=_||yt.getFullYear(),B=0;_&&!N||(B=N>0?N-1:yt.getMonth());var U,j=O||0,at=G||0,K=F||0,st=Q||0;return it?new Date(Date.UTC(ut,B,gt,j,at,K,st+60*it.offset*1e3)):s?new Date(Date.UTC(ut,B,gt,j,at,K,st)):(U=new Date(ut,B,gt,j,at,K,st),lt&&(U=z(U).week(lt).toDate()),U)}catch{return new Date("")}})($,u,p,W),this.init(),m&&m!==!0&&(this.$L=this.locale(m).$L),k&&$!=this.format(u)&&(this.$d=new Date("")),E={}}else if(u instanceof Array)for(var o=u.length,l=1;l<=o;l+=1){h[1]=u[l-1];var y=W.apply(this,h);if(y.isValid()){this.$d=y.$d,this.$L=y.$L,this.init();break}l===o&&(this.$d=new Date(""))}else R.call(this,H)}}}))})(St)),St.exports}var Ar=$r();const Fr=Yt(Ar);var Ct={exports:{}},Yr=Ct.exports,ye;function Lr(){return ye||(ye=1,(function(t,e){(function(i,n){t.exports=n()})(Yr,(function(){return function(i,n){var a=n.prototype,f=a.format;a.format=function(d){var T=this,E=this.$locale();if(!this.isValid())return f.bind(this)(d);var A=this.$utils(),b=(d||"YYYY-MM-DDTHH:mm:ssZ").replace(/\[([^\]]+)]|Q|wo|ww|w|WW|W|zzz|z|gggg|GGGG|Do|X|x|k{1,2}|S/g,(function(L){switch(L){case"Q":return Math.ceil((T.$M+1)/3);case"Do":return E.ordinal(T.$D);case"gggg":return T.weekYear();case"GGGG":return T.isoWeekYear();case"wo":return E.ordinal(T.week(),"W");case"w":case"ww":return A.s(T.week(),L==="w"?1:2,"0");case"W":case"WW":return A.s(T.isoWeek(),L==="W"?1:2,"0");case"k":case"kk":return A.s(String(T.$H===0?24:T.$H),L==="k"?1:2,"0");case"X":return Math.floor(T.$d.getTime()/1e3);case"x":return T.$d.getTime();case"z":return"["+T.offsetName()+"]";case"zzz":return"["+T.offsetName("long")+"]";default:return L}}));return f.bind(this)(b)}}}))})(Ct)),Ct.exports}var Or=Lr();const Wr=Yt(Or);var Mt={exports:{}},Pr=Mt.exports,ge;function Nr(){return ge||(ge=1,(function(t,e){(function(i,n){t.exports=n()})(Pr,(function(){var i,n,a=1e3,f=6e4,d=36e5,T=864e5,E=/\[([^\]]+)]|Y{1,4}|M{1,4}|D{1,2}|d{1,4}|H{1,2}|h{1,2}|a|A|m{1,2}|s{1,2}|Z{1,2}|SSS/g,A=31536e6,b=2628e6,L=/^(-|\+)?P(?:([-+]?[0-9,.]*)Y)?(?:([-+]?[0-9,.]*)M)?(?:([-+]?[0-9,.]*)W)?(?:([-+]?[0-9,.]*)D)?(?:T(?:([-+]?[0-9,.]*)H)?(?:([-+]?[0-9,.]*)M)?(?:([-+]?[0-9,.]*)S)?)?$/,C={years:A,months:b,days:T,hours:d,minutes:f,seconds:a,milliseconds:1,weeks:6048e5},D=function($){return $ instanceof R},V=function($,p,h){return new R($,h,p.$l)},I=function($){return n.p($)+"s"},S=function($){return $<0},M=function($){return S($)?Math.ceil($):Math.floor($)},W=function($){return Math.abs($)},Y=function($,p){return $?S($)?{negative:!0,format:""+W($)+p}:{negative:!1,format:""+$+p}:{negative:!1,format:""}},R=(function(){function $(h,u,x){var v=this;if(this.$d={},this.$l=x,h===void 0&&(this.$ms=0,this.parseFromMilliseconds()),u)return V(h*C[I(u)],this);if(typeof h=="number")return this.$ms=h,this.parseFromMilliseconds(),this;if(typeof h=="object")return Object.keys(h).forEach((function(o){v.$d[I(o)]=h[o]})),this.calMilliseconds(),this;if(typeof h=="string"){var k=h.match(L);if(k){var m=k.slice(2).map((function(o){return o!=null?Number(o):0}));return this.$d.years=m[0],this.$d.months=m[1],this.$d.weeks=m[2],this.$d.days=m[3],this.$d.hours=m[4],this.$d.minutes=m[5],this.$d.seconds=m[6],this.calMilliseconds(),this}}return this}var p=$.prototype;return p.calMilliseconds=function(){var h=this;this.$ms=Object.keys(this.$d).reduce((function(u,x){return u+(h.$d[x]||0)*C[x]}),0)},p.parseFromMilliseconds=function(){var h=this.$ms;this.$d.years=M(h/A),h%=A,this.$d.months=M(h/b),h%=b,this.$d.days=M(h/T),h%=T,this.$d.hours=M(h/d),h%=d,this.$d.minutes=M(h/f),h%=f,this.$d.seconds=M(h/a),h%=a,this.$d.milliseconds=h},p.toISOString=function(){var h=Y(this.$d.years,"Y"),u=Y(this.$d.months,"M"),x=+this.$d.days||0;this.$d.weeks&&(x+=7*this.$d.weeks);var v=Y(x,"D"),k=Y(this.$d.hours,"H"),m=Y(this.$d.minutes,"M"),o=this.$d.seconds||0;this.$d.milliseconds&&(o+=this.$d.milliseconds/1e3,o=Math.round(1e3*o)/1e3);var l=Y(o,"S"),y=h.negative||u.negative||v.negative||k.negative||m.negative||l.negative,g=k.format||m.format||l.format?"T":"",w=(y?"-":"")+"P"+h.format+u.format+v.format+g+k.format+m.format+l.format;return w==="P"||w==="-P"?"P0D":w},p.toJSON=function(){return this.toISOString()},p.format=function(h){var u=h||"YYYY-MM-DDTHH:mm:ss",x={Y:this.$d.years,YY:n.s(this.$d.years,2,"0"),YYYY:n.s(this.$d.years,4,"0"),M:this.$d.months,MM:n.s(this.$d.months,2,"0"),D:this.$d.days,DD:n.s(this.$d.days,2,"0"),H:this.$d.hours,HH:n.s(this.$d.hours,2,"0"),m:this.$d.minutes,mm:n.s(this.$d.minutes,2,"0"),s:this.$d.seconds,ss:n.s(this.$d.seconds,2,"0"),SSS:n.s(this.$d.milliseconds,3,"0")};return u.replace(E,(function(v,k){return k||String(x[v])}))},p.as=function(h){return this.$ms/C[I(h)]},p.get=function(h){var u=this.$ms,x=I(h);return x==="milliseconds"?u%=1e3:u=x==="weeks"?M(u/C[x]):this.$d[x],u||0},p.add=function(h,u,x){var v;return v=u?h*C[I(u)]:D(h)?h.$ms:V(h,this).$ms,V(this.$ms+v*(x?-1:1),this)},p.subtract=function(h,u){return this.add(h,u,!0)},p.locale=function(h){var u=this.clone();return u.$l=h,u},p.clone=function(){return V(this.$ms,this)},p.humanize=function(h){return i().add(this.$ms,"ms").locale(this.$l).fromNow(!h)},p.valueOf=function(){return this.asMilliseconds()},p.milliseconds=function(){return this.get("milliseconds")},p.asMilliseconds=function(){return this.as("milliseconds")},p.seconds=function(){return this.get("seconds")},p.asSeconds=function(){return this.as("seconds")},p.minutes=function(){return this.get("minutes")},p.asMinutes=function(){return this.as("minutes")},p.hours=function(){return this.get("hours")},p.asHours=function(){return this.as("hours")},p.days=function(){return this.get("days")},p.asDays=function(){return this.as("days")},p.weeks=function(){return this.get("weeks")},p.asWeeks=function(){return this.as("weeks")},p.months=function(){return this.get("months")},p.asMonths=function(){return this.as("months")},p.years=function(){return this.get("years")},p.asYears=function(){return this.as("years")},$})(),H=function($,p,h){return $.add(p.years()*h,"y").add(p.months()*h,"M").add(p.days()*h,"d").add(p.hours()*h,"h").add(p.minutes()*h,"m").add(p.seconds()*h,"s").add(p.milliseconds()*h,"ms")};return function($,p,h){i=h,n=h().$utils(),h.duration=function(v,k){var m=h.locale();return V(v,{$l:m},k)},h.isDuration=D;var u=p.prototype.add,x=p.prototype.subtract;p.prototype.add=function(v,k){return D(v)?H(this,v,1):u.bind(this)(v,k)},p.prototype.subtract=function(v,k){return D(v)?H(this,v,-1):x.bind(this)(v,k)}}}))})(Mt)),Mt.exports}var zr=Nr();const Vr=Yt(zr);var qt=(function(){var t=c(function(m,o,l,y){for(l=l||{},y=m.length;y--;l[m[y]]=o);return l},"o"),e=[6,8,10,12,13,14,15,16,17,18,20,21,22,23,24,25,26,27,28,29,30,31,33,35,36,38,40],i=[1,26],n=[1,27],a=[1,28],f=[1,29],d=[1,30],T=[1,31],E=[1,32],A=[1,33],b=[1,34],L=[1,9],C=[1,10],D=[1,11],V=[1,12],I=[1,13],S=[1,14],M=[1,15],W=[1,16],Y=[1,19],R=[1,20],H=[1,21],$=[1,22],p=[1,23],h=[1,25],u=[1,35],x={trace:c(function(){},"trace"),yy:{},symbols_:{error:2,start:3,gantt:4,document:5,EOF:6,line:7,SPACE:8,statement:9,NL:10,weekday:11,weekday_monday:12,weekday_tuesday:13,weekday_wednesday:14,weekday_thursday:15,weekday_friday:16,weekday_saturday:17,weekday_sunday:18,weekend:19,weekend_friday:20,weekend_saturday:21,dateFormat:22,inclusiveEndDates:23,topAxis:24,axisFormat:25,tickInterval:26,excludes:27,includes:28,todayMarker:29,title:30,acc_title:31,acc_title_value:32,acc_descr:33,acc_descr_value:34,acc_descr_multiline_value:35,section:36,clickStatement:37,taskTxt:38,taskData:39,click:40,callbackname:41,callbackargs:42,href:43,clickStatementDebug:44,$accept:0,$end:1},terminals_:{2:"error",4:"gantt",6:"EOF",8:"SPACE",10:"NL",12:"weekday_monday",13:"weekday_tuesday",14:"weekday_wednesday",15:"weekday_thursday",16:"weekday_friday",17:"weekday_saturday",18:"weekday_sunday",20:"weekend_friday",21:"weekend_saturday",22:"dateFormat",23:"inclusiveEndDates",24:"topAxis",25:"axisFormat",26:"tickInterval",27:"excludes",28:"includes",29:"todayMarker",30:"title",31:"acc_title",32:"acc_title_value",33:"acc_descr",34:"acc_descr_value",35:"acc_descr_multiline_value",36:"section",38:"taskTxt",39:"taskData",40:"click",41:"callbackname",42:"callbackargs",43:"href"},productions_:[0,[3,3],[5,0],[5,2],[7,2],[7,1],[7,1],[7,1],[11,1],[11,1],[11,1],[11,1],[11,1],[11,1],[11,1],[19,1],[19,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,1],[9,2],[9,2],[9,1],[9,1],[9,1],[9,2],[37,2],[37,3],[37,3],[37,4],[37,3],[37,4],[37,2],[44,2],[44,3],[44,3],[44,4],[44,3],[44,4],[44,2]],performAction:c(function(o,l,y,g,w,s,z){var r=s.length-1;switch(w){case 1:return s[r-1];case 2:this.$=[];break;case 3:s[r-1].push(s[r]),this.$=s[r-1];break;case 4:case 5:this.$=s[r];break;case 6:case 7:this.$=[];break;case 8:g.setWeekday("monday");break;case 9:g.setWeekday("tuesday");break;case 10:g.setWeekday("wednesday");break;case 11:g.setWeekday("thursday");break;case 12:g.setWeekday("friday");break;case 13:g.setWeekday("saturday");break;case 14:g.setWeekday("sunday");break;case 15:g.setWeekend("friday");break;case 16:g.setWeekend("saturday");break;case 17:g.setDateFormat(s[r].substr(11)),this.$=s[r].substr(11);break;case 18:g.enableInclusiveEndDates(),this.$=s[r].substr(18);break;case 19:g.TopAxis(),this.$=s[r].substr(8);break;case 20:g.setAxisFormat(s[r].substr(11)),this.$=s[r].substr(11);break;case 21:g.setTickInterval(s[r].substr(13)),this.$=s[r].substr(13);break;case 22:g.setExcludes(s[r].substr(9)),this.$=s[r].substr(9);break;case 23:g.setIncludes(s[r].substr(9)),this.$=s[r].substr(9);break;case 24:g.setTodayMarker(s[r].substr(12)),this.$=s[r].substr(12);break;case 27:g.setDiagramTitle(s[r].substr(6)),this.$=s[r].substr(6);break;case 28:this.$=s[r].trim(),g.setAccTitle(this.$);break;case 29:case 30:this.$=s[r].trim(),g.setAccDescription(this.$);break;case 31:g.addSection(s[r].substr(8)),this.$=s[r].substr(8);break;case 33:g.addTask(s[r-1],s[r]),this.$="task";break;case 34:this.$=s[r-1],g.setClickEvent(s[r-1],s[r],null);break;case 35:this.$=s[r-2],g.setClickEvent(s[r-2],s[r-1],s[r]);break;case 36:this.$=s[r-2],g.setClickEvent(s[r-2],s[r-1],null),g.setLink(s[r-2],s[r]);break;case 37:this.$=s[r-3],g.setClickEvent(s[r-3],s[r-2],s[r-1]),g.setLink(s[r-3],s[r]);break;case 38:this.$=s[r-2],g.setClickEvent(s[r-2],s[r],null),g.setLink(s[r-2],s[r-1]);break;case 39:this.$=s[r-3],g.setClickEvent(s[r-3],s[r-1],s[r]),g.setLink(s[r-3],s[r-2]);break;case 40:this.$=s[r-1],g.setLink(s[r-1],s[r]);break;case 41:case 47:this.$=s[r-1]+" "+s[r];break;case 42:case 43:case 45:this.$=s[r-2]+" "+s[r-1]+" "+s[r];break;case 44:case 46:this.$=s[r-3]+" "+s[r-2]+" "+s[r-1]+" "+s[r];break}},"anonymous"),table:[{3:1,4:[1,2]},{1:[3]},t(e,[2,2],{5:3}),{6:[1,4],7:5,8:[1,6],9:7,10:[1,8],11:17,12:i,13:n,14:a,15:f,16:d,17:T,18:E,19:18,20:A,21:b,22:L,23:C,24:D,25:V,26:I,27:S,28:M,29:W,30:Y,31:R,33:H,35:$,36:p,37:24,38:h,40:u},t(e,[2,7],{1:[2,1]}),t(e,[2,3]),{9:36,11:17,12:i,13:n,14:a,15:f,16:d,17:T,18:E,19:18,20:A,21:b,22:L,23:C,24:D,25:V,26:I,27:S,28:M,29:W,30:Y,31:R,33:H,35:$,36:p,37:24,38:h,40:u},t(e,[2,5]),t(e,[2,6]),t(e,[2,17]),t(e,[2,18]),t(e,[2,19]),t(e,[2,20]),t(e,[2,21]),t(e,[2,22]),t(e,[2,23]),t(e,[2,24]),t(e,[2,25]),t(e,[2,26]),t(e,[2,27]),{32:[1,37]},{34:[1,38]},t(e,[2,30]),t(e,[2,31]),t(e,[2,32]),{39:[1,39]},t(e,[2,8]),t(e,[2,9]),t(e,[2,10]),t(e,[2,11]),t(e,[2,12]),t(e,[2,13]),t(e,[2,14]),t(e,[2,15]),t(e,[2,16]),{41:[1,40],43:[1,41]},t(e,[2,4]),t(e,[2,28]),t(e,[2,29]),t(e,[2,33]),t(e,[2,34],{42:[1,42],43:[1,43]}),t(e,[2,40],{41:[1,44]}),t(e,[2,35],{43:[1,45]}),t(e,[2,36]),t(e,[2,38],{42:[1,46]}),t(e,[2,37]),t(e,[2,39])],defaultActions:{},parseError:c(function(o,l){if(l.recoverable)this.trace(o);else{var y=new Error(o);throw y.hash=l,y}},"parseError"),parse:c(function(o){var l=this,y=[0],g=[],w=[null],s=[],z=this.table,r="",_=0,N=0,P=2,O=1,G=s.slice.call(arguments,1),F=Object.create(this.lexer),Q={yy:{}};for(var it in this.yy)Object.prototype.hasOwnProperty.call(this.yy,it)&&(Q.yy[it]=this.yy[it]);F.setInput(o,Q.yy),Q.yy.lexer=F,Q.yy.parser=this,typeof F.yylloc>"u"&&(F.yylloc={});var lt=F.yylloc;s.push(lt);var yt=F.options&&F.options.ranges;typeof Q.yy.parseError=="function"?this.parseError=Q.yy.parseError:this.parseError=Object.getPrototypeOf(this).parseError;function gt(Z){y.length=y.length-2*Z,w.length=w.length-Z,s.length=s.length-Z}c(gt,"popStack");function ut(){var Z;return Z=g.pop()||F.lex()||O,typeof Z!="number"&&(Z instanceof Array&&(g=Z,Z=g.pop()),Z=l.symbols_[Z]||Z),Z}c(ut,"lex");for(var B,U,j,at,K={},st,J,se,Tt;;){if(U=y[y.length-1],this.defaultActions[U]?j=this.defaultActions[U]:((B===null||typeof B>"u")&&(B=ut()),j=z[U]&&z[U][B]),typeof j>"u"||!j.length||!j[0]){var Lt="";Tt=[];for(st in z[U])this.terminals_[st]&&st>P&&Tt.push("'"+this.terminals_[st]+"'");F.showPosition?Lt="Parse error on line "+(_+1)+`:
`+F.showPosition()+`
Expecting `+Tt.join(", ")+", got '"+(this.terminals_[B]||B)+"'":Lt="Parse error on line "+(_+1)+": Unexpected "+(B==O?"end of input":"'"+(this.terminals_[B]||B)+"'"),this.parseError(Lt,{text:F.match,token:this.terminals_[B]||B,line:F.yylineno,loc:lt,expected:Tt})}if(j[0]instanceof Array&&j.length>1)throw new Error("Parse Error: multiple actions possible at state: "+U+", token: "+B);switch(j[0]){case 1:y.push(B),w.push(F.yytext),s.push(F.yylloc),y.push(j[1]),B=null,N=F.yyleng,r=F.yytext,_=F.yylineno,lt=F.yylloc;break;case 2:if(J=this.productions_[j[1]][1],K.$=w[w.length-J],K._$={first_line:s[s.length-(J||1)].first_line,last_line:s[s.length-1].last_line,first_column:s[s.length-(J||1)].first_column,last_column:s[s.length-1].last_column},yt&&(K._$.range=[s[s.length-(J||1)].range[0],s[s.length-1].range[1]]),at=this.performAction.apply(K,[r,N,_,Q.yy,j[1],w,s].concat(G)),typeof at<"u")return at;J&&(y=y.slice(0,-1*J*2),w=w.slice(0,-1*J),s=s.slice(0,-1*J)),y.push(this.productions_[j[1]][0]),w.push(K.$),s.push(K._$),se=z[y[y.length-2]][y[y.length-1]],y.push(se);break;case 3:return!0}}return!0},"parse")},v=(function(){var m={EOF:1,parseError:c(function(l,y){if(this.yy.parser)this.yy.parser.parseError(l,y);else throw new Error(l)},"parseError"),setInput:c(function(o,l){return this.yy=l||this.yy||{},this._input=o,this._more=this._backtrack=this.done=!1,this.yylineno=this.yyleng=0,this.yytext=this.matched=this.match="",this.conditionStack=["INITIAL"],this.yylloc={first_line:1,first_column:0,last_line:1,last_column:0},this.options.ranges&&(this.yylloc.range=[0,0]),this.offset=0,this},"setInput"),input:c(function(){var o=this._input[0];this.yytext+=o,this.yyleng++,this.offset++,this.match+=o,this.matched+=o;var l=o.match(/(?:\r\n?|\n).*/g);return l?(this.yylineno++,this.yylloc.last_line++):this.yylloc.last_column++,this.options.ranges&&this.yylloc.range[1]++,this._input=this._input.slice(1),o},"input"),unput:c(function(o){var l=o.length,y=o.split(/(?:\r\n?|\n)/g);this._input=o+this._input,this.yytext=this.yytext.substr(0,this.yytext.length-l),this.offset-=l;var g=this.match.split(/(?:\r\n?|\n)/g);this.match=this.match.substr(0,this.match.length-1),this.matched=this.matched.substr(0,this.matched.length-1),y.length-1&&(this.yylineno-=y.length-1);var w=this.yylloc.range;return this.yylloc={first_line:this.yylloc.first_line,last_line:this.yylineno+1,first_column:this.yylloc.first_column,last_column:y?(y.length===g.length?this.yylloc.first_column:0)+g[g.length-y.length].length-y[0].length:this.yylloc.first_column-l},this.options.ranges&&(this.yylloc.range=[w[0],w[0]+this.yyleng-l]),this.yyleng=this.yytext.length,this},"unput"),more:c(function(){return this._more=!0,this},"more"),reject:c(function(){if(this.options.backtrack_lexer)this._backtrack=!0;else return this.parseError("Lexical error on line "+(this.yylineno+1)+`. You can only invoke reject() in the lexer when the lexer is of the backtracking persuasion (options.backtrack_lexer = true).
`+this.showPosition(),{text:"",token:null,line:this.yylineno});return this},"reject"),less:c(function(o){this.unput(this.match.slice(o))},"less"),pastInput:c(function(){var o=this.matched.substr(0,this.matched.length-this.match.length);return(o.length>20?"...":"")+o.substr(-20).replace(/\n/g,"")},"pastInput"),upcomingInput:c(function(){var o=this.match;return o.length<20&&(o+=this._input.substr(0,20-o.length)),(o.substr(0,20)+(o.length>20?"...":"")).replace(/\n/g,"")},"upcomingInput"),showPosition:c(function(){var o=this.pastInput(),l=new Array(o.length+1).join("-");return o+this.upcomingInput()+`
`+l+"^"},"showPosition"),test_match:c(function(o,l){var y,g,w;if(this.options.backtrack_lexer&&(w={yylineno:this.yylineno,yylloc:{first_line:this.yylloc.first_line,last_line:this.last_line,first_column:this.yylloc.first_column,last_column:this.yylloc.last_column},yytext:this.yytext,match:this.match,matches:this.matches,matched:this.matched,yyleng:this.yyleng,offset:this.offset,_more:this._more,_input:this._input,yy:this.yy,conditionStack:this.conditionStack.slice(0),done:this.done},this.options.ranges&&(w.yylloc.range=this.yylloc.range.slice(0))),g=o[0].match(/(?:\r\n?|\n).*/g),g&&(this.yylineno+=g.length),this.yylloc={first_line:this.yylloc.last_line,last_line:this.yylineno+1,first_column:this.yylloc.last_column,last_column:g?g[g.length-1].length-g[g.length-1].match(/\r?\n?/)[0].length:this.yylloc.last_column+o[0].length},this.yytext+=o[0],this.match+=o[0],this.matches=o,this.yyleng=this.yytext.length,this.options.ranges&&(this.yylloc.range=[this.offset,this.offset+=this.yyleng]),this._more=!1,this._backtrack=!1,this._input=this._input.slice(o[0].length),this.matched+=o[0],y=this.performAction.call(this,this.yy,this,l,this.conditionStack[this.conditionStack.length-1]),this.done&&this._input&&(this.done=!1),y)return y;if(this._backtrack){for(var s in w)this[s]=w[s];return!1}return!1},"test_match"),next:c(function(){if(this.done)return this.EOF;this._input||(this.done=!0);var o,l,y,g;this._more||(this.yytext="",this.match="");for(var w=this._currentRules(),s=0;s<w.length;s++)if(y=this._input.match(this.rules[w[s]]),y&&(!l||y[0].length>l[0].length)){if(l=y,g=s,this.options.backtrack_lexer){if(o=this.test_match(y,w[s]),o!==!1)return o;if(this._backtrack){l=!1;continue}else return!1}else if(!this.options.flex)break}return l?(o=this.test_match(l,w[g]),o!==!1?o:!1):this._input===""?this.EOF:this.parseError("Lexical error on line "+(this.yylineno+1)+`. Unrecognized text.
`+this.showPosition(),{text:"",token:null,line:this.yylineno})},"next"),lex:c(function(){var l=this.next();return l||this.lex()},"lex"),begin:c(function(l){this.conditionStack.push(l)},"begin"),popState:c(function(){var l=this.conditionStack.length-1;return l>0?this.conditionStack.pop():this.conditionStack[0]},"popState"),_currentRules:c(function(){return this.conditionStack.length&&this.conditionStack[this.conditionStack.length-1]?this.conditions[this.conditionStack[this.conditionStack.length-1]].rules:this.conditions.INITIAL.rules},"_currentRules"),topState:c(function(l){return l=this.conditionStack.length-1-Math.abs(l||0),l>=0?this.conditionStack[l]:"INITIAL"},"topState"),pushState:c(function(l){this.begin(l)},"pushState"),stateStackSize:c(function(){return this.conditionStack.length},"stateStackSize"),options:{"case-insensitive":!0},performAction:c(function(l,y,g,w){switch(g){case 0:return this.begin("open_directive"),"open_directive";case 1:return this.begin("acc_title"),31;case 2:return this.popState(),"acc_title_value";case 3:return this.begin("acc_descr"),33;case 4:return this.popState(),"acc_descr_value";case 5:this.begin("acc_descr_multiline");break;case 6:this.popState();break;case 7:return"acc_descr_multiline_value";case 8:break;case 9:break;case 10:break;case 11:return 10;case 12:break;case 13:break;case 14:this.begin("href");break;case 15:this.popState();break;case 16:return 43;case 17:this.begin("callbackname");break;case 18:this.popState();break;case 19:this.popState(),this.begin("callbackargs");break;case 20:return 41;case 21:this.popState();break;case 22:return 42;case 23:this.begin("click");break;case 24:this.popState();break;case 25:return 40;case 26:return 4;case 27:return 22;case 28:return 23;case 29:return 24;case 30:return 25;case 31:return 26;case 32:return 28;case 33:return 27;case 34:return 29;case 35:return 12;case 36:return 13;case 37:return 14;case 38:return 15;case 39:return 16;case 40:return 17;case 41:return 18;case 42:return 20;case 43:return 21;case 44:return"date";case 45:return 30;case 46:return"accDescription";case 47:return 36;case 48:return 38;case 49:return 39;case 50:return":";case 51:return 6;case 52:return"INVALID"}},"anonymous"),rules:[/^(?:%%\{)/i,/^(?:accTitle\s*:\s*)/i,/^(?:(?!\n||)*[^\n]*)/i,/^(?:accDescr\s*:\s*)/i,/^(?:(?!\n||)*[^\n]*)/i,/^(?:accDescr\s*\{\s*)/i,/^(?:[\}])/i,/^(?:[^\}]*)/i,/^(?:%%(?!\{)*[^\n]*)/i,/^(?:[^\}]%%*[^\n]*)/i,/^(?:%%*[^\n]*[\n]*)/i,/^(?:[\n]+)/i,/^(?:\s+)/i,/^(?:%[^\n]*)/i,/^(?:href[\s]+["])/i,/^(?:["])/i,/^(?:[^"]*)/i,/^(?:call[\s]+)/i,/^(?:\([\s]*\))/i,/^(?:\()/i,/^(?:[^(]*)/i,/^(?:\))/i,/^(?:[^)]*)/i,/^(?:click[\s]+)/i,/^(?:[\s\n])/i,/^(?:[^\s\n]*)/i,/^(?:gantt\b)/i,/^(?:dateFormat\s[^#\n;]+)/i,/^(?:inclusiveEndDates\b)/i,/^(?:topAxis\b)/i,/^(?:axisFormat\s[^#\n;]+)/i,/^(?:tickInterval\s[^#\n;]+)/i,/^(?:includes\s[^#\n;]+)/i,/^(?:excludes\s[^#\n;]+)/i,/^(?:todayMarker\s[^\n;]+)/i,/^(?:weekday\s+monday\b)/i,/^(?:weekday\s+tuesday\b)/i,/^(?:weekday\s+wednesday\b)/i,/^(?:weekday\s+thursday\b)/i,/^(?:weekday\s+friday\b)/i,/^(?:weekday\s+saturday\b)/i,/^(?:weekday\s+sunday\b)/i,/^(?:weekend\s+friday\b)/i,/^(?:weekend\s+saturday\b)/i,/^(?:\d\d\d\d-\d\d-\d\d\b)/i,/^(?:title\s[^\n]+)/i,/^(?:accDescription\s[^#\n;]+)/i,/^(?:section\s[^\n]+)/i,/^(?:[^:\n]+)/i,/^(?::[^#\n;]+)/i,/^(?::)/i,/^(?:$)/i,/^(?:.)/i],conditions:{acc_descr_multiline:{rules:[6,7],inclusive:!1},acc_descr:{rules:[4],inclusive:!1},acc_title:{rules:[2],inclusive:!1},callbackargs:{rules:[21,22],inclusive:!1},callbackname:{rules:[18,19,20],inclusive:!1},href:{rules:[15,16],inclusive:!1},click:{rules:[24,25],inclusive:!1},INITIAL:{rules:[0,1,3,5,8,9,10,11,12,13,14,17,23,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52],inclusive:!0}}};return m})();x.lexer=v;function k(){this.yy={}}return c(k,"Parser"),k.prototype=x,x.Parser=k,new k})();qt.parser=qt;var Rr=qt;X.extend(Er);X.extend(Fr);X.extend(Wr);var pe={friday:5,saturday:6},tt="",Zt="",Ut=void 0,Qt="",pt=[],vt=[],Kt=new Map,Jt=[],At=[],kt="",te="",Fe=["active","done","crit","milestone","vert"],ee=[],dt="",xt=!1,re=!1,ne="sunday",Ft="saturday",Gt=0,Hr=c(function(){Jt=[],At=[],kt="",ee=[],Et=0,jt=void 0,It=void 0,q=[],tt="",Zt="",te="",Ut=void 0,Qt="",pt=[],vt=[],xt=!1,re=!1,Gt=0,Kt=new Map,dt="",Ke(),ne="sunday",Ft="saturday"},"clear"),Br=c(function(t){dt=t},"setDiagramId"),qr=c(function(t){Zt=t},"setAxisFormat"),Gr=c(function(){return Zt},"getAxisFormat"),Xr=c(function(t){Ut=t},"setTickInterval"),jr=c(function(){return Ut},"getTickInterval"),Zr=c(function(t){Qt=t},"setTodayMarker"),Ur=c(function(){return Qt},"getTodayMarker"),Qr=c(function(t){tt=t},"setDateFormat"),Kr=c(function(){xt=!0},"enableInclusiveEndDates"),Jr=c(function(){return xt},"endDatesAreInclusive"),tn=c(function(){re=!0},"enableTopAxis"),en=c(function(){return re},"topAxisEnabled"),rn=c(function(t){te=t},"setDisplayMode"),nn=c(function(){return te},"getDisplayMode"),sn=c(function(){return tt},"getDateFormat"),an=c(function(t){pt=t.toLowerCase().split(/[\s,]+/)},"setIncludes"),on=c(function(){return pt},"getIncludes"),cn=c(function(t){vt=t.toLowerCase().split(/[\s,]+/)},"setExcludes"),ln=c(function(){return vt},"getExcludes"),un=c(function(){return Kt},"getLinks"),dn=c(function(t){kt=t,Jt.push(t)},"addSection"),fn=c(function(){return Jt},"getSections"),hn=c(function(){let t=ve();const e=10;let i=0;for(;!t&&i<e;)t=ve(),i++;return At=q,At},"getTasks"),Ye=c(function(t,e,i,n){const a=t.format(e.trim()),f=t.format("YYYY-MM-DD");return n.includes(a)||n.includes(f)?!1:i.includes("weekends")&&(t.isoWeekday()===pe[Ft]||t.isoWeekday()===pe[Ft]+1)||i.includes(t.format("dddd").toLowerCase())?!0:i.includes(a)||i.includes(f)},"isInvalidDate"),mn=c(function(t){ne=t},"setWeekday"),kn=c(function(){return ne},"getWeekday"),yn=c(function(t){Ft=t},"setWeekend"),Le=c(function(t,e,i,n){if(!i.length||t.manualEndTime)return;let a;t.startTime instanceof Date?a=X(t.startTime):a=X(t.startTime,e,!0),a=a.add(1,"d");let f;t.endTime instanceof Date?f=X(t.endTime):f=X(t.endTime,e,!0);const[d,T]=gn(a,f,e,i,n);t.endTime=d.toDate(),t.renderEndTime=T},"checkTaskDates"),gn=c(function(t,e,i,n,a){let f=!1,d=null;for(;t<=e;)f||(d=e.toDate()),f=Ye(t,i,n,a),f&&(e=e.add(1,"d")),t=t.add(1,"d");return[e,d]},"fixTaskDates"),Xt=c(function(t,e,i){if(i=i.trim(),c(T=>{const E=T.trim();return E==="x"||E==="X"},"isTimestampFormat")(e)&&/^\d+$/.test(i))return new Date(Number(i));const f=/^after\s+(?<ids>[\d\w- ]+)/.exec(i);if(f!==null){let T=null;for(const A of f.groups.ids.split(" ")){let b=ct(A);b!==void 0&&(!T||b.endTime>T.endTime)&&(T=b)}if(T)return T.endTime;const E=new Date;return E.setHours(0,0,0,0),E}let d=X(i,e.trim(),!0);if(d.isValid())return d.toDate();{ot.debug("Invalid date:"+i),ot.debug("With date format:"+e.trim());const T=new Date(i);if(T===void 0||isNaN(T.getTime())||T.getFullYear()<-1e4||T.getFullYear()>1e4)throw new Error("Invalid date:"+i);return T}},"getStartDate"),Oe=c(function(t){const e=/^(\d+(?:\.\d+)?)([Mdhmswy]|ms)$/.exec(t.trim());return e!==null?[Number.parseFloat(e[1]),e[2]]:[NaN,"ms"]},"parseDuration"),We=c(function(t,e,i,n=!1){i=i.trim();const f=/^until\s+(?<ids>[\d\w- ]+)/.exec(i);if(f!==null){let b=null;for(const C of f.groups.ids.split(" ")){let D=ct(C);D!==void 0&&(!b||D.startTime<b.startTime)&&(b=D)}if(b)return b.startTime;const L=new Date;return L.setHours(0,0,0,0),L}let d=X(i,e.trim(),!0);if(d.isValid())return n&&(d=d.add(1,"d")),d.toDate();let T=X(t);const[E,A]=Oe(i);if(!Number.isNaN(E)){const b=T.add(E,A);b.isValid()&&(T=b)}return T.toDate()},"getEndDate"),Et=0,mt=c(function(t){return t===void 0?(Et=Et+1,"task"+Et):t},"parseId"),pn=c(function(t,e){let i;e.substr(0,1)===":"?i=e.substr(1,e.length):i=e;const n=i.split(","),a={};ie(n,a,Fe);for(let d=0;d<n.length;d++)n[d]=n[d].trim();let f="";switch(n.length){case 1:a.id=mt(),a.startTime=t.endTime,f=n[0];break;case 2:a.id=mt(),a.startTime=Xt(void 0,tt,n[0]),f=n[1];break;case 3:a.id=mt(n[0]),a.startTime=Xt(void 0,tt,n[1]),f=n[2];break}return f&&(a.endTime=We(a.startTime,tt,f,xt),a.manualEndTime=X(f,"YYYY-MM-DD",!0).isValid(),Le(a,tt,vt,pt)),a},"compileData"),vn=c(function(t,e){let i;e.substr(0,1)===":"?i=e.substr(1,e.length):i=e;const n=i.split(","),a={};ie(n,a,Fe);for(let f=0;f<n.length;f++)n[f]=n[f].trim();switch(n.length){case 1:a.id=mt(),a.startTime={type:"prevTaskEnd",id:t},a.endTime={data:n[0]};break;case 2:a.id=mt(),a.startTime={type:"getStartDate",startData:n[0]},a.endTime={data:n[1]};break;case 3:a.id=mt(n[0]),a.startTime={type:"getStartDate",startData:n[1]},a.endTime={data:n[2]};break}return a},"parseData"),jt,It,q=[],Pe={},xn=c(function(t,e){const i={section:kt,type:kt,processed:!1,manualEndTime:!1,renderEndTime:null,raw:{data:e},task:t,classes:[]},n=vn(It,e);i.raw.startTime=n.startTime,i.raw.endTime=n.endTime,i.id=n.id,i.prevTaskId=It,i.active=n.active,i.done=n.done,i.crit=n.crit,i.milestone=n.milestone,i.vert=n.vert,i.order=Gt,Gt++;const a=q.push(i);It=i.id,Pe[i.id]=a-1},"addTask"),ct=c(function(t){const e=Pe[t];return q[e]},"findTaskById"),Tn=c(function(t,e){const i={section:kt,type:kt,description:t,task:t,classes:[]},n=pn(jt,e);i.startTime=n.startTime,i.endTime=n.endTime,i.id=n.id,i.active=n.active,i.done=n.done,i.crit=n.crit,i.milestone=n.milestone,i.vert=n.vert,jt=i,At.push(i)},"addTaskOrg"),ve=c(function(){const t=c(function(i){const n=q[i];let a="";switch(q[i].raw.startTime.type){case"prevTaskEnd":{const f=ct(n.prevTaskId);n.startTime=f.endTime;break}case"getStartDate":a=Xt(void 0,tt,q[i].raw.startTime.startData),a&&(q[i].startTime=a);break}return q[i].startTime&&(q[i].endTime=We(q[i].startTime,tt,q[i].raw.endTime.data,xt),q[i].endTime&&(q[i].processed=!0,q[i].manualEndTime=X(q[i].raw.endTime.data,"YYYY-MM-DD",!0).isValid(),Le(q[i],tt,vt,pt))),q[i].processed},"compileTask");let e=!0;for(const[i,n]of q.entries())t(i),e=e&&n.processed;return e},"compileTasks"),bn=c(function(t,e){let i=e;ft().securityLevel!=="loose"&&(i=Qe.sanitizeUrl(e)),t.split(",").forEach(function(n){ct(n)!==void 0&&(ze(n,()=>{window.open(i,"_self")}),Kt.set(n,i))}),Ne(t,"clickable")},"setLink"),Ne=c(function(t,e){t.split(",").forEach(function(i){let n=ct(i);n!==void 0&&n.classes.push(e)})},"setClass"),wn=c(function(t,e,i){if(ft().securityLevel!=="loose"||e===void 0)return;let n=[];if(typeof i=="string"){n=i.split(/,(?=(?:(?:[^"]*"){2})*[^"]*$)/);for(let f=0;f<n.length;f++){let d=n[f].trim();d.startsWith('"')&&d.endsWith('"')&&(d=d.substr(1,d.length-2)),n[f]=d}}n.length===0&&n.push(t),ct(t)!==void 0&&ze(t,()=>{Je.runFunc(e,...n)})},"setClickFun"),ze=c(function(t,e){ee.push(function(){const i=dt?`${dt}-${t}`:t,n=document.querySelector(`[id="${i}"]`);n!==null&&n.addEventListener("click",function(){e()})},function(){const i=dt?`${dt}-${t}`:t,n=document.querySelector(`[id="${i}-text"]`);n!==null&&n.addEventListener("click",function(){e()})})},"pushFun"),_n=c(function(t,e,i){t.split(",").forEach(function(n){wn(n,e,i)}),Ne(t,"clickable")},"setClickEvent"),Dn=c(function(t){ee.forEach(function(e){e(t)})},"bindFunctions"),Sn={getConfig:c(()=>ft().gantt,"getConfig"),clear:Hr,setDateFormat:Qr,getDateFormat:sn,enableInclusiveEndDates:Kr,endDatesAreInclusive:Jr,enableTopAxis:tn,topAxisEnabled:en,setAxisFormat:qr,getAxisFormat:Gr,setTickInterval:Xr,getTickInterval:jr,setTodayMarker:Zr,getTodayMarker:Ur,setAccTitle:je,getAccTitle:Xe,setDiagramTitle:Ge,getDiagramTitle:qe,setDiagramId:Br,setDisplayMode:rn,getDisplayMode:nn,setAccDescription:Be,getAccDescription:He,addSection:dn,getSections:fn,getTasks:hn,addTask:xn,findTaskById:ct,addTaskOrg:Tn,setIncludes:an,getIncludes:on,setExcludes:cn,getExcludes:ln,setClickEvent:_n,setLink:bn,getLinks:un,bindFunctions:Dn,parseDuration:Oe,isInvalidDate:Ye,setWeekday:mn,getWeekday:kn,setWeekend:yn};function ie(t,e,i){let n=!0;for(;n;)n=!1,i.forEach(function(a){const f="^\\s*"+a+"\\s*$",d=new RegExp(f);t[0].match(d)&&(e[a]=!0,t.shift(1),n=!0)})}c(ie,"getTaskTags");X.extend(Vr);var Cn=c(function(){ot.debug("Something is calling, setConf, remove the call")},"setConf"),xe={monday:lr,tuesday:cr,wednesday:or,thursday:ar,friday:sr,saturday:ir,sunday:nr},Mn=c((t,e)=>{let i=[...t].map(()=>-1/0),n=[...t].sort((f,d)=>f.startTime-d.startTime||f.order-d.order),a=0;for(const f of n)for(let d=0;d<i.length;d++)if(f.startTime>=i[d]){i[d]=f.endTime,f.order=d+e,d>a&&(a=d);break}return a},"getMaxIntersections"),rt,Rt=1e4,En=c(function(t,e,i,n){const a=ft().gantt;n.db.setDiagramId(e);const f=ft().securityLevel;let d;f==="sandbox"&&(d=bt("#i"+e));const T=f==="sandbox"?bt(d.nodes()[0].contentDocument.body):bt("body"),E=f==="sandbox"?d.nodes()[0].contentDocument:document,A=E.getElementById(e);rt=A.parentElement.offsetWidth,rt===void 0&&(rt=1200),a.useWidth!==void 0&&(rt=a.useWidth);const b=n.db.getTasks();let L=[];for(const u of b)L.push(u.type);L=h(L);const C={};let D=2*a.topPadding;if(n.db.getDisplayMode()==="compact"||a.displayMode==="compact"){const u={};for(const v of b)u[v.section]===void 0?u[v.section]=[v]:u[v.section].push(v);let x=0;for(const v of Object.keys(u)){const k=Mn(u[v],x)+1;x+=k,D+=k*(a.barHeight+a.barGap),C[v]=k}}else{D+=b.length*(a.barHeight+a.barGap);for(const u of L)C[u]=b.filter(x=>x.type===u).length}A.setAttribute("viewBox","0 0 "+rt+" "+D);const V=T.select(`[id="${e}"]`),I=tr().domain([er(b,function(u){return u.startTime}),rr(b,function(u){return u.endTime})]).rangeRound([0,rt-a.leftPadding-a.rightPadding]);function S(u,x){const v=u.startTime,k=x.startTime;let m=0;return v>k?m=1:v<k&&(m=-1),m}c(S,"taskCompare"),b.sort(S),M(b,rt,D),Ze(V,D,rt,a.useMaxWidth),V.append("text").text(n.db.getDiagramTitle()).attr("x",rt/2).attr("y",a.titleTopMargin).attr("class","titleText");function M(u,x,v){const k=a.barHeight,m=k+a.barGap,o=a.topPadding,l=a.leftPadding,y=ur().domain([0,L.length]).range(["#00B9FA","#F95002"]).interpolate(Dr);Y(m,o,l,x,v,u,n.db.getExcludes(),n.db.getIncludes()),H(l,o,x,v),W(u,m,o,l,k,y,x),$(m,o),p(l,o,x,v)}c(M,"makeGantt");function W(u,x,v,k,m,o,l){u.sort((r,_)=>r.vert===_.vert?0:r.vert?1:-1);const g=[...new Set(u.map(r=>r.order))].map(r=>u.find(_=>_.order===r));V.append("g").selectAll("rect").data(g).enter().append("rect").attr("x",0).attr("y",function(r,_){return _=r.order,_*x+v-2}).attr("width",function(){return l-a.rightPadding/2}).attr("height",x).attr("class",function(r){for(const[_,N]of L.entries())if(r.type===N)return"section section"+_%a.numberSectionStyles;return"section section0"}).enter();const w=V.append("g").selectAll("rect").data(u).enter(),s=n.db.getLinks();if(w.append("rect").attr("id",function(r){return e+"-"+r.id}).attr("rx",3).attr("ry",3).attr("x",function(r){return r.milestone?I(r.startTime)+k+.5*(I(r.endTime)-I(r.startTime))-.5*m:I(r.startTime)+k}).attr("y",function(r,_){return _=r.order,r.vert?a.gridLineStartPadding:_*x+v}).attr("width",function(r){return r.milestone?m:r.vert?.08*m:I(r.renderEndTime||r.endTime)-I(r.startTime)}).attr("height",function(r){return r.vert?b.length*(a.barHeight+a.barGap)+a.barHeight*2:m}).attr("transform-origin",function(r,_){return _=r.order,(I(r.startTime)+k+.5*(I(r.endTime)-I(r.startTime))).toString()+"px "+(_*x+v+.5*m).toString()+"px"}).attr("class",function(r){const _="task";let N="";r.classes.length>0&&(N=r.classes.join(" "));let P=0;for(const[G,F]of L.entries())r.type===F&&(P=G%a.numberSectionStyles);let O="";return r.active?r.crit?O+=" activeCrit":O=" active":r.done?r.crit?O=" doneCrit":O=" done":r.crit&&(O+=" crit"),O.length===0&&(O=" task"),r.milestone&&(O=" milestone "+O),r.vert&&(O=" vert "+O),O+=P,O+=" "+N,_+O}),w.append("text").attr("id",function(r){return e+"-"+r.id+"-text"}).text(function(r){return r.task}).attr("font-size",a.fontSize).attr("x",function(r){let _=I(r.startTime),N=I(r.renderEndTime||r.endTime);if(r.milestone&&(_+=.5*(I(r.endTime)-I(r.startTime))-.5*m,N=_+m),r.vert)return I(r.startTime)+k;const P=this.getBBox().width;return P>N-_?N+P+1.5*a.leftPadding>l?_+k-5:N+k+5:(N-_)/2+_+k}).attr("y",function(r,_){return r.vert?a.gridLineStartPadding+b.length*(a.barHeight+a.barGap)+60:(_=r.order,_*x+a.barHeight/2+(a.fontSize/2-2)+v)}).attr("text-height",m).attr("class",function(r){const _=I(r.startTime);let N=I(r.endTime);r.milestone&&(N=_+m);const P=this.getBBox().width;let O="";r.classes.length>0&&(O=r.classes.join(" "));let G=0;for(const[Q,it]of L.entries())r.type===it&&(G=Q%a.numberSectionStyles);let F="";return r.active&&(r.crit?F="activeCritText"+G:F="activeText"+G),r.done?r.crit?F=F+" doneCritText"+G:F=F+" doneText"+G:r.crit&&(F=F+" critText"+G),r.milestone&&(F+=" milestoneText"),r.vert&&(F+=" vertText"),P>N-_?N+P+1.5*a.leftPadding>l?O+" taskTextOutsideLeft taskTextOutside"+G+" "+F:O+" taskTextOutsideRight taskTextOutside"+G+" "+F+" width-"+P:O+" taskText taskText"+G+" "+F+" width-"+P}),ft().securityLevel==="sandbox"){let r;r=bt("#i"+e);const _=r.nodes()[0].contentDocument;w.filter(function(N){return s.has(N.id)}).each(function(N){var P=_.querySelector("#"+CSS.escape(e+"-"+N.id)),O=_.querySelector("#"+CSS.escape(e+"-"+N.id+"-text"));const G=P.parentNode;var F=_.createElement("a");F.setAttribute("xlink:href",s.get(N.id)),F.setAttribute("target","_top"),G.appendChild(F),F.appendChild(P),F.appendChild(O)})}}c(W,"drawRects");function Y(u,x,v,k,m,o,l,y){if(l.length===0&&y.length===0)return;let g,w;for(const{startTime:P,endTime:O}of o)(g===void 0||P<g)&&(g=P),(w===void 0||O>w)&&(w=O);if(!g||!w)return;if(X(w).diff(X(g),"year")>5){ot.warn("The difference between the min and max time is more than 5 years. This will cause performance issues. Skipping drawing exclude days.");return}const s=n.db.getDateFormat(),z=[];let r=null,_=X(g);for(;_.valueOf()<=w;)n.db.isInvalidDate(_,s,l,y)?r?r.end=_:r={start:_,end:_}:r&&(z.push(r),r=null),_=_.add(1,"d");V.append("g").selectAll("rect").data(z).enter().append("rect").attr("id",P=>e+"-exclude-"+P.start.format("YYYY-MM-DD")).attr("x",P=>I(P.start.startOf("day"))+v).attr("y",a.gridLineStartPadding).attr("width",P=>I(P.end.endOf("day"))-I(P.start.startOf("day"))).attr("height",m-x-a.gridLineStartPadding).attr("transform-origin",function(P,O){return(I(P.start)+v+.5*(I(P.end)-I(P.start))).toString()+"px "+(O*u+.5*m).toString()+"px"}).attr("class","exclude-range")}c(Y,"drawExcludeDays");function R(u,x,v,k){if(v<=0||u>x)return 1/0;const m=x-u,o=X.duration({[k??"day"]:v}).asMilliseconds();return o<=0?1/0:Math.ceil(m/o)}c(R,"getEstimatedTickCount");function H(u,x,v,k){const m=n.db.getDateFormat(),o=n.db.getAxisFormat();let l;o?l=o:m==="D"?l="%d":l=a.axisFormat??"%Y-%m-%d";let y=pr(I).tickSize(-k+x+a.gridLineStartPadding).tickFormat(ae(l));const w=/^([1-9]\d*)(millisecond|second|minute|hour|day|week|month)$/.exec(n.db.getTickInterval()||a.tickInterval);if(w!==null){const s=parseInt(w[1],10);if(isNaN(s)||s<=0)ot.warn(`Invalid tick interval value: "${w[1]}". Skipping custom tick interval.`);else{const z=w[2],r=n.db.getWeekday()||a.weekday,_=I.domain(),N=_[0],P=_[1],O=R(N,P,s,z);if(O>Rt)ot.warn(`The tick interval "${s}${z}" would generate ${O} ticks, which exceeds the maximum allowed (${Rt}). This may indicate an invalid date or time range. Skipping custom tick interval.`);else switch(z){case"millisecond":y.ticks(fe.every(s));break;case"second":y.ticks(de.every(s));break;case"minute":y.ticks(ue.every(s));break;case"hour":y.ticks(le.every(s));break;case"day":y.ticks(ce.every(s));break;case"week":y.ticks(xe[r].every(s));break;case"month":y.ticks(oe.every(s));break}}}if(V.append("g").attr("class","grid").attr("transform","translate("+u+", "+(k-50)+")").call(y).selectAll("text").style("text-anchor","middle").attr("fill","#000").attr("stroke","none").attr("font-size",10).attr("dy","1em"),n.db.topAxisEnabled()||a.topAxis){let s=gr(I).tickSize(-k+x+a.gridLineStartPadding).tickFormat(ae(l));if(w!==null){const z=parseInt(w[1],10);if(isNaN(z)||z<=0)ot.warn(`Invalid tick interval value: "${w[1]}". Skipping custom tick interval.`);else{const r=w[2],_=n.db.getWeekday()||a.weekday,N=I.domain(),P=N[0],O=N[1];if(R(P,O,z,r)<=Rt)switch(r){case"millisecond":s.ticks(fe.every(z));break;case"second":s.ticks(de.every(z));break;case"minute":s.ticks(ue.every(z));break;case"hour":s.ticks(le.every(z));break;case"day":s.ticks(ce.every(z));break;case"week":s.ticks(xe[_].every(z));break;case"month":s.ticks(oe.every(z));break}}}V.append("g").attr("class","grid").attr("transform","translate("+u+", "+x+")").call(s).selectAll("text").style("text-anchor","middle").attr("fill","#000").attr("stroke","none").attr("font-size",10)}}c(H,"makeGrid");function $(u,x){let v=0;const k=Object.keys(C).map(m=>[m,C[m]]);V.append("g").selectAll("text").data(k).enter().append(function(m){const o=m[0].split(Ue.lineBreakRegex),l=-(o.length-1)/2,y=E.createElementNS("http://www.w3.org/2000/svg","text");y.setAttribute("dy",l+"em");for(const[g,w]of o.entries()){const s=E.createElementNS("http://www.w3.org/2000/svg","tspan");s.setAttribute("alignment-baseline","central"),s.setAttribute("x","10"),g>0&&s.setAttribute("dy","1em"),s.textContent=w,y.appendChild(s)}return y}).attr("x",10).attr("y",function(m,o){if(o>0)for(let l=0;l<o;l++)return v+=k[o-1][1],m[1]*u/2+v*u+x;else return m[1]*u/2+x}).attr("font-size",a.sectionFontSize).attr("class",function(m){for(const[o,l]of L.entries())if(m[0]===l)return"sectionTitle sectionTitle"+o%a.numberSectionStyles;return"sectionTitle"})}c($,"vertLabels");function p(u,x,v,k){const m=n.db.getTodayMarker();if(m==="off")return;const o=V.append("g").attr("class","today"),l=new Date,y=o.append("line");y.attr("x1",I(l)+u).attr("x2",I(l)+u).attr("y1",a.titleTopMargin).attr("y2",k-a.titleTopMargin).attr("class","today"),m!==""&&y.attr("style",m.replace(/,/g,";"))}c(p,"drawToday");function h(u){const x={},v=[];for(let k=0,m=u.length;k<m;++k)Object.prototype.hasOwnProperty.call(x,u[k])||(x[u[k]]=!0,v.push(u[k]));return v}c(h,"checkUnique")},"draw"),In={setConf:Cn,draw:En},$n=c(t=>`
  .mermaid-main-font {
        font-family: ${t.fontFamily};
  }

  .exclude-range {
    fill: ${t.excludeBkgColor};
  }

  .section {
    stroke: none;
    opacity: 0.2;
  }

  .section0 {
    fill: ${t.sectionBkgColor};
  }

  .section2 {
    fill: ${t.sectionBkgColor2};
  }

  .section1,
  .section3 {
    fill: ${t.altSectionBkgColor};
    opacity: 0.2;
  }

  .sectionTitle0 {
    fill: ${t.titleColor};
  }

  .sectionTitle1 {
    fill: ${t.titleColor};
  }

  .sectionTitle2 {
    fill: ${t.titleColor};
  }

  .sectionTitle3 {
    fill: ${t.titleColor};
  }

  .sectionTitle {
    text-anchor: start;
    font-family: ${t.fontFamily};
  }


  /* Grid and axis */

  .grid .tick {
    stroke: ${t.gridColor};
    opacity: 0.8;
    shape-rendering: crispEdges;
  }

  .grid .tick text {
    font-family: ${t.fontFamily};
    fill: ${t.textColor};
  }

  .grid path {
    stroke-width: 0;
  }


  /* Today line */

  .today {
    fill: none;
    stroke: ${t.todayLineColor};
    stroke-width: 2px;
  }


  /* Task styling */

  /* Default task */

  .task {
    stroke-width: 2;
  }

  .taskText {
    text-anchor: middle;
    font-family: ${t.fontFamily};
  }

  .taskTextOutsideRight {
    fill: ${t.taskTextDarkColor};
    text-anchor: start;
    font-family: ${t.fontFamily};
  }

  .taskTextOutsideLeft {
    fill: ${t.taskTextDarkColor};
    text-anchor: end;
  }


  /* Special case clickable */

  .task.clickable {
    cursor: pointer;
  }

  .taskText.clickable {
    cursor: pointer;
    fill: ${t.taskTextClickableColor} !important;
    font-weight: bold;
  }

  .taskTextOutsideLeft.clickable {
    cursor: pointer;
    fill: ${t.taskTextClickableColor} !important;
    font-weight: bold;
  }

  .taskTextOutsideRight.clickable {
    cursor: pointer;
    fill: ${t.taskTextClickableColor} !important;
    font-weight: bold;
  }


  /* Specific task settings for the sections*/

  .taskText0,
  .taskText1,
  .taskText2,
  .taskText3 {
    fill: ${t.taskTextColor};
  }

  .task0,
  .task1,
  .task2,
  .task3 {
    fill: ${t.taskBkgColor};
    stroke: ${t.taskBorderColor};
  }

  .taskTextOutside0,
  .taskTextOutside2
  {
    fill: ${t.taskTextOutsideColor};
  }

  .taskTextOutside1,
  .taskTextOutside3 {
    fill: ${t.taskTextOutsideColor};
  }


  /* Active task */

  .active0,
  .active1,
  .active2,
  .active3 {
    fill: ${t.activeTaskBkgColor};
    stroke: ${t.activeTaskBorderColor};
  }

  .activeText0,
  .activeText1,
  .activeText2,
  .activeText3 {
    fill: ${t.taskTextDarkColor} !important;
  }


  /* Completed task */

  .done0,
  .done1,
  .done2,
  .done3 {
    stroke: ${t.doneTaskBorderColor};
    fill: ${t.doneTaskBkgColor};
    stroke-width: 2;
  }

  .doneText0,
  .doneText1,
  .doneText2,
  .doneText3 {
    fill: ${t.taskTextDarkColor} !important;
  }

  /* Done task text displayed outside the bar sits against the diagram background,
     not against the done-task bar, so it must use the outside/contrast color. */
  .doneText0.taskTextOutsideLeft,
  .doneText0.taskTextOutsideRight,
  .doneText1.taskTextOutsideLeft,
  .doneText1.taskTextOutsideRight,
  .doneText2.taskTextOutsideLeft,
  .doneText2.taskTextOutsideRight,
  .doneText3.taskTextOutsideLeft,
  .doneText3.taskTextOutsideRight {
    fill: ${t.taskTextOutsideColor} !important;
  }


  /* Tasks on the critical line */

  .crit0,
  .crit1,
  .crit2,
  .crit3 {
    stroke: ${t.critBorderColor};
    fill: ${t.critBkgColor};
    stroke-width: 2;
  }

  .activeCrit0,
  .activeCrit1,
  .activeCrit2,
  .activeCrit3 {
    stroke: ${t.critBorderColor};
    fill: ${t.activeTaskBkgColor};
    stroke-width: 2;
  }

  .doneCrit0,
  .doneCrit1,
  .doneCrit2,
  .doneCrit3 {
    stroke: ${t.critBorderColor};
    fill: ${t.doneTaskBkgColor};
    stroke-width: 2;
    cursor: pointer;
    shape-rendering: crispEdges;
  }

  .milestone {
    transform: rotate(45deg) scale(0.8,0.8);
  }

  .milestoneText {
    font-style: italic;
  }
  .doneCritText0,
  .doneCritText1,
  .doneCritText2,
  .doneCritText3 {
    fill: ${t.taskTextDarkColor} !important;
  }

  /* Done-crit task text outside the bar — same reasoning as doneText above. */
  .doneCritText0.taskTextOutsideLeft,
  .doneCritText0.taskTextOutsideRight,
  .doneCritText1.taskTextOutsideLeft,
  .doneCritText1.taskTextOutsideRight,
  .doneCritText2.taskTextOutsideLeft,
  .doneCritText2.taskTextOutsideRight,
  .doneCritText3.taskTextOutsideLeft,
  .doneCritText3.taskTextOutsideRight {
    fill: ${t.taskTextOutsideColor} !important;
  }

  .vert {
    stroke: ${t.vertLineColor};
  }

  .vertText {
    font-size: 15px;
    text-anchor: middle;
    fill: ${t.vertLineColor} !important;
  }

  .activeCritText0,
  .activeCritText1,
  .activeCritText2,
  .activeCritText3 {
    fill: ${t.taskTextDarkColor} !important;
  }

  .titleText {
    text-anchor: middle;
    font-size: 18px;
    fill: ${t.titleColor||t.textColor};
    font-family: ${t.fontFamily};
  }
`,"getStyles"),An=$n,Pn={parser:Rr,db:Sn,renderer:In,styles:An};export{Pn as diagram};
