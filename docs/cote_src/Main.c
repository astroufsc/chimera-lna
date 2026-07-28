//*************************************************************************************
//Programa de controle da cupula do telescópio Meade de 40cm do OPD
//*************************************************************************************

//*************************************************************************************
//INCLUDES
#include	"AT89S52.h"
#include 	<stdio.h>
#include	<string.h>
#include	<stdlib.h>
//**************************************************************************************

//**************************************************************************************
//DEFINIÇÔES
#define ETIQQTD      182          // Numero de etiquetas
#define ETIQINI      801          // Numero da etiqueta inicial
#define RESOL        1            // Resolução em etiquetas do posicionamento
#define OFFSET       56           // Posição do LCB com a trapeira no Norte
#define STR_MAX      15           // Tamanho maximo p/comando(e resposta)do PC
#define DIG_LCB      6            // Quantidade de caracteres lidos pelo LCB
#define JOGSET       12           // Distancia do destino para ligar JOG do inversor
#define CR           13
#define PC           1            // Identifica comunicação com PC
#define LCB          0            // Identifica comunicação com LCB
#define ATIVAR       0            // Sinal do uC para energizar reles
#define CORTAR       1            // Sinal do uC para desenergizar reles
#define PISCA        200          // (ms)Tempo para piscar leds de comunicação
#define LCB_ON       3000         // (ms)Delay para leitura do LCB após ser energizado
#define LCBTMO       6000         // (ms)Timeout para leitura da posição da cupula
#define TRAPTMO      12000        // (ms)Timeout para abrir/fechar trapeira (medido:8sec)
//Valores para timer0 gerar interrupção a cada 1ms
#define H1MS         0xFC         // 256-(unsigned char)(((int)(XTAL/12L/1000L/256L)+12L)>>8)&0xFF);
#define L1MS         0x6A         // 256-(unsigned char)(((int)(XTAL/12L/1000L-((long)(256-TH0))*256L);
//ClocK do microcontrolador
#define XTAL         11160000L    
// Baud-rate do leitor de códigos de barras e do PC de controle do Meade
#define BAUD         9600L

//*******************************************************************************************
//VARIAVEIS GLOBAIS
char etiqueta[7];                 // Guarda a ultima etiqueta lida
bit  alarm;                       // Indica que um comando não pode ser executado
char cmdpc[STR_MAX];              // Armazena string de comunicação com o PC

//*******************************************************************************************

//*******************************************************************************************
//PROTOTIPO DE FUNÇÕES
void ini_cup(void);
char pos_cup(void);
bit waitRI(int timeout);
void abrirtrapeira(void);
void fechartrapeira(void);
void movecup(int alvo);
void init_hdw(void);
void delayms(unsigned int ms);
void pisca_lcb(void);
void pisca_pc(void);
//void mon_cup(void);
//********************************************************************************************
//RENOMEANDO OS BITS DO MICROCONTROLADOR
//BITS P3
sbit Rxd       =       0xB0;
sbit Txd       =       0xB1;
sbit int_lcb   =  	   0xB2;
sbit int_pc    =       0xB3;
sbit SelMux    =       0xB4;     // =0 le lcb,=1 comunica pc
sbit swfecha   =       0xB6;     // =0 qdo.trapeira fechando
sbit swabre    =       0xB7;     // =0 qdo.trapeira abrindo
//BITS P2
sbit inv_on    =       0xA0;
sbit jog       =       0xA1;
sbit ccw       =       0xA2;
sbit cw        =       0xA3;
sbit flat      =       0xA4;
sbit abret     =       0xA5;
sbit fechat    =       0xA6;
sbit lcb_on    =       0xA7;
//BITS P0
sbit led_pc    =       0x80;    // =0 Acende led comunicação pc
sbit led_lcb   =       0x81;    // =0 Acende led comunicação lcd
sbit reles     =       0x87;    // =0 Reles habilitados,=1 reles off

// INICIO DO MAIN
void main (void)
{
	//Inicializações de variáveis
	char *pcmd;
	int cont_cmd=STR_MAX-1;        // Contador de caracteres do comando do PC
	// Inicializações
init_hdw();                    // Inicilização do hardware
//Inicialização da Cupula
ini_cup();                     // Ler posição atual(resultado em etiqueta)
// Seleção do Mux para conversa com PC
SelMux=PC;
//puts("ENTRE COM O COMANDO");    // Usar apenas teste do loops
pcmd=cmdpc;
TI=1;
//Executar continuamente
while(1)
{
		//Testa quantidade de caracteres recebidos
		if(cont_cmd==0)         // cont_cmd: contador decrescente
		{
			pcmd=cmdpc;
			cont_cmd=STR_MAX;
		}
		//Aguarda recepção de caracter do PC
		while(!RI);
		RI=0;
		alarm=0;                //Desativa flag de alarme 
		*pcmd=SBUF;				//Armazena caracter do LCB na string etiqueta
		  if(*pcmd==CR)		    //teclado[i]=CR=´\0´
		  {  *pcmd='\0';          //Convenção de final de string no C
		      pisca_pc();          //Pisca led de comunicação com o PC
		      if(strcmp(cmdpc,"ABRIR")==0) //Compara o comando abrir com pegacarac
		      {     puts("ABRINDO\r");
			       abrirtrapeira();
				   puts("ABERTO\r");
//				   pisca_pc();	      //Pisca led de comunicação com o PC
				   pcmd=cmdpc;
				   cont_cmd=STR_MAX;
				   continue;
			  }
		           if(strcmp(cmdpc,"FECHAR")==0) //Compara pega caracter com fechar 
		           {     puts("FECHANDO\r");
			             fechartrapeira();
				          puts("FECHADO\r");
//				          pisca_pc();
				          pcmd=cmdpc;
				          cont_cmd=STR_MAX;
				          continue;
			       }
		           if(strcmp(cmdpc,"POSICAO?")==0)
		           {     strcpy(cmdpc,"CUPULA=");
			             strcat(cmdpc,etiqueta);
				         puts(cmdpc);
				         pcmd=cmdpc;
				         cont_cmd=STR_MAX;
				         continue;
		           }
		           if(strncmp(cmdpc,"CUPULA=",7)==0)
		           {     puts("GIRANDO\r");
			             pcmd=strchr(cmdpc,'=')+1;
				         movecup(atoi(pcmd));
				         if(alarm) 
				            puts("ALARME\r");
				         else
				         {       strcpy(pcmd,etiqueta);
				                 puts(cmdpc);
				         }
//				          pisca_pc();
				          pcmd=cmdpc;
				          cont_cmd=STR_MAX;
				          continue;
			        }
					if(strcmp(cmdpc,"FLAT_ON")==0) //Compara o comando ligar flat-field
					{
						flat = 1;
						puts("FLAT_LIGADO\r");
						pcmd=cmdpc;
						cont_cmd=STR_MAX;
						continue;
					}
					if(strcmp(cmdpc,"FLAT_OFF")==0) //Compara o comando desligar flat-field
					{	flat = 0;
						puts("FLAT_DESLIGADO\r");
						pcmd=cmdpc;
						cont_cmd=STR_MAX;
						continue;
					}
		        	if(strcmp(cmdpc,"INICIAR")==0)   //Le proxima etiqueta
			        {    puts("INICIANDO\r");
			             ini_cup();                //Ler posição atual(resultado em etiqueta)
				         strcpy(cmdpc,"CUPULA=");
				         strcat(cmdpc,etiqueta);
				         puts(cmdpc);
//				         pisca_pc();
				         pcmd=cmdpc;
				         cont_cmd=STR_MAX;
				         continue;
			        }
			       if(strcmp(cmdpc,"MONITORAR")==0)
			        {   puts("MONITORANDO\r");
//			            mon_cup();
				        puts("FIM_MONITOR\r");
//				        pisca_pc();
				        pcmd=cmdpc;
				        cont_cmd=STR_MAX;
	                    continue;
				    }
			       if(strcmp(cmdpc,"GIRARCW")==0)   //Compara pega caracter com fechar
	                {  ccw=0;
                       puts("GIRANDOCW\r");
                       cw=1;
                       pcmd=cmdpc;
                       cont_cmd=STR_MAX;
                       continue;
                    }
                   if(strcmp(cmdpc,"GIRARCCW")==0)   //Compara pega caracter com fechar
                    {  cw=0;
                       puts("GIRANDOCCW\r");
                       ccw=1;
                       pcmd=cmdpc;
                       cont_cmd=STR_MAX;
                       continue;
                    }
                   if(strcmp(cmdpc,"ATIVARJOG")==0) //Compara pega caracter com fechar
                    {  puts("ATIVANDOJOG\r");
                       cw=ccw=0;
                       jog=1;
                       pcmd=cmdpc;
                       cont_cmd=STR_MAX;
                       continue;
                    }
                   if(strcmp(cmdpc,"DESATIVARJOG")==0)  //Compara pega caracter com fechar
                    {  puts("DESATIVANDOJOG\r");
                       jog=0;
                       pcmd=cmdpc;
                       cont_cmd=STR_MAX;
                       continue;
                    }
                   if(strcmp(cmdpc,"PARAR")==0)        //Compara pega caracter com fechar
                    {  puts("PARANDO\r");
                       P2=0x01;
                       pcmd=cmdpc;
                       cont_cmd=STR_MAX;
                       continue;
                    }
                    else
                    {  puts("INVALIDO\r");
                       pcmd=cmdpc;
                       cont_cmd=STR_MAX;
                       continue;
                    }
             }
             else
             {   *pcmd++=SBUF;                      //Guarda caracter recebido
		          cont_cmd--;
             }
       }
//       init_hdw();
}

//******************************************************************************************
void movecup(int alvo)
{ 
       int  dist;                 //diferença entre posições alvo e atual
       //Liga LCB e chaveia comunicação serial para o LCB
       SelMux=LCB;               //Comunicação com o LCB
       lcb_on=1;                //Energiza o LCB
       RI=0;
       IE1=0;
       do
        { //Calcula a distancia ate a posição alvo
          dist=alvo - atoi(etiqueta);
          if(dist>ETIQQTD/2)
		  dist=ETIQQTD;
       if(dist<-ETIQQTD/2)
         dist+=ETIQQTD;
         //Aciona o motor da cupula no sentido do menor caminho
       if(dist>0)
         {
           ccw=0;
           cw=1;
         }
         else
         {
           cw=0;
           ccw=1;
         }
         delayms(200);     //Tempo para o inversos reconhecer sentido de giro em caso de JOG
         //Encerra loop se posição de destino foi alcançada
		 if(dist<0)
                 dist*=-1;
         if(dist<RESOL)
                 break;
         //Ativa JOG do inversor se estiverperto da posição final
         if(dist<JOGSET)
           {    cw=ccw=0;
                jog=1;
           }
           else
               jog=0; 
        //Pega posição atual da cupula
         if(pos_cup()==1)
           {    //Encerra loop se ocorrer timeout na leitura
             	alarm=1;
                break;
           }
         //Se Pc enviar algum caracter,encerrar
          if(IE1)
          {    IE1=0;
               break;
          }
   }while(1);
   //Desativa reles
	cw = 0;
	ccw = 0;
	jog = 0;
	lcb_on = 0;
   SelMux=PC;    //Volta a comunicação com o PC
}
//******************************************************************************************
//ini_cup: Coloca na string o valor lido pelo LCB na posição atual da cupula.Se o LCB não
// ler nenhuma posição gira a cupula no sentido horário até ler a próxima posição.Se demorar
// mais que LCBTMO para ler posição etiqueta = "ERROR\r"
//*******************************************************************************************
void ini_cup(void)
{
     SelMux = LCB;             //Seleciona comunicação para o LCB
	 lcb_on=1;               //Liga alimentação do LCB
	 RI = 0;
	 //Gira cupula baixa velocidade
	 jog=cw=1;
	 delayms(200);            //Tempo para o inversor perceber o sentido de rotação
	 cw=0;
	 //Faz uma leitura de etiqueta
	 pos_cup();
	 //Para cupula e desenergiza LCB
	cw = 0;
	ccw = 0;
	jog = 0;
	lcb_on = 0;
	 SelMux=PC;               //Retorna Comunicação para o PC
}
//*******************************************************************************************
//pos_cup: Coloca na string etiqueta o valor lido pelo LCB da posição atual da cupula e retorna
// zero.Supoe que LCB esteja ligado.Se demorar mais que LCBTMO para ler a pisição,
//etiqueta = "ERROR\r",retornando 1.
//*******************************************************************************************
char pos_cup(void)
{
     char   *petiq;
	 int    contador=DIG_LCB;
	 petiq=etiqueta;
	 //Le a etiqueta
	 do
	 {
	      //Aguarda chegada de caracter do LCB
		  if(waitRI(LCBTMO))
		  {        strcpy(etiqueta,"ERRO\r");
		           return 1;
		  }
		  *petiq=SBUF;
		  if(((contador==1)&&(*petiq!='\n'))||((contador==2)&&(*petiq!='\r')))
		  {        petiq=etiqueta;
		           contador=DIG_LCB+1;
		  }
		  else
		           petiq++;
		  }        while(--contador>0);
		           etiqueta[5]='\0';       //Remove LF da leitura
				   contador=atoi(etiqueta)-ETIQINI-OFFSET;
				   if(contador<0)
				             contador+=ETIQQTD;
				   sprintf(etiqueta,"%04d\r",contador);
				   pisca_lcb();
				   return 0;
		  }
//*******************************************************************************************
//waitRI: Aguarda RI=1 com timeout.
//        INPUT: timeout(inteiro,milisegundos)
//        OUTPUT:=1->timeout,=0->RI detectado
//*******************************************************************************************
bit waitRI(int timeout)
{     ET0=0;             //Desabilita interrupção de timer0
      TF0=1;
	  do
	    {   
		   //Recarrega timer0 a cada 1 milisegundo
		   if(TF0)
		   {  
		      TR0=TF0=0;
			  if(--timeout==0)
			  return(1);
			  TH0=H1MS;	        //Carrega periodo de 1 milisegundo
			  TL0=L1MS;
			  TR0=1;
			}
	     }while(!RI);
	      RI=TR0=TF0=0;			  //Para timer0
		  return(0);
}
void abrirtrapeira(void)
{
       fechat=0;                  //Segurança desativa rele de fechar trapeira
	   abret=1;					  //Ativa rele que abre a trapeira
	   delayms(TRAPTMO);          //Delay para energização do sensor da trapeira
	   abret=0;
}
//******************************************************************************************
//fechartrapeira:Aciona o motor da trapeira até que a chave de fim de curso da trapeira 
//fechada(swfecha)atue.
//******************************************************************************************   
void fechartrapeira(void)
{
       abret=0;                  //Segurança desativa rele de abrir trapeira
	   fechat=1;					  //Ativa rele que fecha a trapeira
	   delayms(TRAPTMO);          //Delay para energização do sensor da trapeira
	   fechat=0;
}
//*******************************************************************************************
//~FUNÇÃO DE INICIALIZAÇÃO DO HARDWARE E CONFIGURAÇÃO DA COMUNICAÇÃO
void init_hdw(void)
{                                //puts("Inicializando o hardware\r\n");
       IE=0;                     //Desabilita todas as interrupções
	   P0=0xFF;                  //Leds off reles desabilitados 
	   P1=0xFB;                  //Memoria serial não selesionada
	   P2=1;                     //Desativa todos os reles
	   P3=0xFF;                  //Bits como input ou função secundaria do pino do uC
	   TCON=0x05;                //Interrupções externas(RxDLCB e PC)por transição
	   SCON=0x50;                /*SCON:mode1,8-bit UART,enable rcvr*/
	   TMOD=0x21;                /*Timer1:mode2,reload;Timer0:16-bit divisor*/
	   TH1=256-(unsigned char)(XTAL/BAUD/12L/32L);
	   TR1=1;                    /*TR1: timer1 run*/
	   TR0=0;                    //Timer0 parado
	   TI=1;                     /*TI: set TI to send first char of UART */
	   reles = ATIVAR;			 //Liga alimentacao dos reles 
}
//********************************************************************************************
void delayms(unsigned int ms)
{
             while(ms)
			 {
			       TH0=H1MS;
				   TL0=L1MS;
				   TR0=1;         //Liga timer
				   while(!TF0);   //Fica esperando overflow
				   TR0=0;
				   TF0=0;
				   ms--;
			  }
}
//*******************************************************************************************
//pisca_lcb: Acende led de comunicação com LCB por pisca milisegundos
//*******************************************************************************************
void pisca_lcb(void)
{
         led_lcb=0;
		 delayms(PISCA);
		 led_lcb=1;
}
//*******************************************************************************************
//pisca_pc: Acende led de comunicação com o PC por PISCA milisegundos
//*******************************************************************************************
void pisca_pc(void)
{
         led_pc=0;
		 delayms(PISCA);
		 led_pc=1;
}
