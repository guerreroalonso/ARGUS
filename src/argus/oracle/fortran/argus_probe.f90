! ARGUS - sonda PENGEOM ("oraculo").
!
! Programa de verificacion que enlaza el pengeom.f del usuario y permite a
! ARGUS comparar su propia lectura de la geometria con la de PENGEOM:
!   * vuelca las tablas internas (superficies, arbol genealogico),
!   * localiza puntos con LOCATE,
!   * sigue rayos con STEP (como el visor 3D de PENELOPE).
! No ejecuta ninguna simulacion.
!
! Ordenes (entrada estandar, una por linea):
!   GEOM    <fichero.geo>
!   REPORT  <fichero de informe (geometry.rep)>
!   LOG     <fichero para la unidad 26 (avisos de STEP; evita fort.26)>
!   PARINP  <n> <v1> ... <vn>          (opcional, antes de LOAD)
!   LOAD                               (llama a GEOMIN)
!   STATUS  <fichero>                  (IRETRN y REASON)
!   TABLES  <fichero>                  (tablas internas en texto)
!   LOCATE                             (siguen 2 lineas: entrada y salida)
!   RAYS    <max. segmentos por rayo>  (siguen 2 lineas: entrada y salida)
!   CTLOC                              (siguen 2 lineas: entrada y salida)
!   QUIT
! Los nombres de fichero ocupan la linea entera (pueden tener blancos o
! barras, que la lectura dirigida por lista de Fortran no admite).
! Formatos binarios (acceso STREAM, orden de bytes nativo):
!   entrada: int32 N, N x 6 float64 (x, y, z, u, v, w)
!   LOCATE : int32 N, N x (int32 IBODY, int32 MAT)
!   RAYS   : por rayo int32 IBODY0, int32 MAT0 (LOCATE en el origen),
!            int32 NSEG y NSEG x (float64 S, int32 IBODY, int32 MAT,
!            int32 KSLAST). S es la distancia acumulada (DSTOT) hasta el
!            punto en que STEP se detiene al entrar en un material nuevo.
!   CTLOC  : int32 N, N x (int32 IBODY, int32 MAT, int32 IX, IY, IZ,
!            float64 DENS). Si el punto cae en la caja CT, CTLOCATE da el
!            voxel (IX, IY, IZ desde 1), su material y su densidad; si no,
!            IX = IY = IZ = 0 y MAT es el de LOCATE.

PROGRAM ARGUS_PROBE
  USE TRACK_mod
  USE PENERROR_mod
  USE PENGEOM_mod
  USE PENCT_mod, ONLY: LCT, IBCT, DC
  IMPLICIT DOUBLE PRECISION (A-H,O-Z), INTEGER*4 (I-N)
  COMMON/QSURF/AXX(NS),AXY(NS),AXZ(NS),AYY(NS),AYZ(NS),AZZ(NS), &
    AX(NS),AY(NS),AZ(NS),A0(NS),NSURF,KPLANE(NS)
  COMMON/QTREE/NBODYS,KMOTH(NB),KDGHT(NB,NXG),KSURF(NB,NXG), &
    KFLAG(NB,NXG),KSP(NS),NWARN
  COMMON/QBODY/KBODY(NB,NXG),KBOMO(NB)
  COMMON/CTGEO0/RXC,RYC,RZC,IXC,IYC,IZC,IAC

  CHARACTER(LEN=1024) :: LINE, GEOFN, REPFN, ARG1, ARG2
  CHARACTER(LEN=16) :: CMD
  DOUBLE PRECISION :: PARINP(1000)
  INTEGER*4 :: NPINP, NMATG, NBOD, N, K, MAXSEG, NSEG, IB0, IM0, JX, JY, JZ
  DOUBLE PRECISION :: DV
  INTEGER*4, ALLOCATABLE :: IB(:), IM(:), SIB(:), SIM(:), SKS(:)
  DOUBLE PRECISION, ALLOCATABLE :: P(:,:), SS(:)
  LOGICAL :: LOADED

  NPINP = 0
  NMATG = 0
  NBOD = 0
  PARINP = 0.0D0
  LOADED = .FALSE.
  GEOFN = ' '
  REPFN = 'geometry.rep'

  DO
    READ(5,'(A)',END=900) LINE
    LINE = ADJUSTL(LINE)
    IF (LEN_TRIM(LINE) == 0) CYCLE
    READ(LINE,*) CMD
    SELECT CASE (TRIM(CMD))
    CASE ('GEOM')
      GEOFN = ADJUSTL(LINE(5:))
    CASE ('REPORT')
      REPFN = ADJUSTL(LINE(7:))
    CASE ('LOG')
      ARG1 = ADJUSTL(LINE(4:))
      OPEN(26, FILE=TRIM(ARG1))
    CASE ('PARINP')
      READ(LINE(7:),*) NPINP, (PARINP(K), K=1,NPINP)
    CASE ('LOAD')
      OPEN(15, FILE=TRIM(GEOFN), STATUS='OLD', IOSTAT=KODE)
      IF (KODE /= 0) THEN
        IRETRN = -1
        REASON = 'ARGUS_PROBE: no se puede abrir el fichero de geometria.'
        CYCLE
      END IF
      OPEN(16, FILE=TRIM(REPFN))
      CALL GEOMIN(PARINP, NPINP, NMATG, NBOD, 15, 16)
      CLOSE(15)
      CLOSE(16)
      LOADED = (IRETRN == 0)
    CASE ('STATUS')
      ARG1 = ADJUSTL(LINE(7:))
      OPEN(17, FILE=TRIM(ARG1))
      WRITE(17,'(I8)') IRETRN
      WRITE(17,'(A)') TRIM(REASON)
      WRITE(17,'(I8)') NMATG
      WRITE(17,'(I8)') NBOD
      CLOSE(17)
    CASE ('TABLES')
      IF (.NOT. LOADED) CYCLE
      ARG1 = ADJUSTL(LINE(7:))
      CALL DUMP_TABLES(TRIM(ARG1))
    CASE ('LOCATE')
      READ(5,'(A)') ARG1
      READ(5,'(A)') ARG2
      ARG1 = ADJUSTL(ARG1)
      ARG2 = ADJUSTL(ARG2)
      IF (.NOT. LOADED) CYCLE
      OPEN(18, FILE=TRIM(ARG1), ACCESS='STREAM', FORM='UNFORMATTED', STATUS='OLD')
      READ(18) N
      ALLOCATE(P(6,N), IB(N), IM(N))
      READ(18) P
      CLOSE(18)
      DO K = 1, N
        X = P(1,K); Y = P(2,K); Z = P(3,K)
        U = P(4,K); V = P(5,K); W = P(6,K)
        CALL LOCATE
        IB(K) = IBODY
        IM(K) = MAT
      END DO
      OPEN(19, FILE=TRIM(ARG2), ACCESS='STREAM', FORM='UNFORMATTED', STATUS='REPLACE')
      WRITE(19) N
      WRITE(19) (IB(K), IM(K), K=1,N)
      CLOSE(19)
      DEALLOCATE(P, IB, IM)
    CASE ('RAYS')
      READ(LINE(5:),*) MAXSEG
      READ(5,'(A)') ARG1
      READ(5,'(A)') ARG2
      ARG1 = ADJUSTL(ARG1)
      ARG2 = ADJUSTL(ARG2)
      IF (.NOT. LOADED) CYCLE
      OPEN(18, FILE=TRIM(ARG1), ACCESS='STREAM', FORM='UNFORMATTED', STATUS='OLD')
      READ(18) N
      ALLOCATE(P(6,N), SS(MAXSEG), SIB(MAXSEG), SIM(MAXSEG), SKS(MAXSEG))
      READ(18) P
      CLOSE(18)
      OPEN(19, FILE=TRIM(ARG2), ACCESS='STREAM', FORM='UNFORMATTED', STATUS='REPLACE')
      DO K = 1, N
        X = P(1,K); Y = P(2,K); Z = P(3,K)
        U = P(4,K); V = P(5,K); W = P(6,K)
        CALL LOCATE
        IB0 = IBODY
        IM0 = MAT
        NSEG = 0
        STOT = 0.0D0
        DO WHILE (NSEG < MAXSEG)
          CALL STEP(1.0D30, DSEF, NCROSS)
          STOT = STOT + DSTOT
          IF (IBODY > NBODYS .OR. MAT == 0) EXIT
          NSEG = NSEG + 1
          SS(NSEG) = STOT
          SIB(NSEG) = IBODY
          SIM(NSEG) = MAT
          SKS(NSEG) = KSLAST
        END DO
        WRITE(19) IB0, IM0, NSEG
        IF (NSEG > 0) WRITE(19) (SS(I), SIB(I), SIM(I), SKS(I), I=1,NSEG)
      END DO
      CLOSE(19)
      DEALLOCATE(P, SS, SIB, SIM, SKS)
    CASE ('CTLOC')
      READ(5,'(A)') ARG1
      READ(5,'(A)') ARG2
      ARG1 = ADJUSTL(ARG1)
      ARG2 = ADJUSTL(ARG2)
      IF (.NOT. LOADED) CYCLE
      OPEN(18, FILE=TRIM(ARG1), ACCESS='STREAM', FORM='UNFORMATTED', STATUS='OLD')
      READ(18) N
      ALLOCATE(P(6,N))
      READ(18) P
      CLOSE(18)
      OPEN(19, FILE=TRIM(ARG2), ACCESS='STREAM', FORM='UNFORMATTED', STATUS='REPLACE')
      WRITE(19) N
      DO K = 1, N
        X = P(1,K); Y = P(2,K); Z = P(3,K)
        U = P(4,K); V = P(5,K); W = P(6,K)
        CALL LOCATE
        IB0 = IBODY
        IM0 = MAT
        JX = 0; JY = 0; JZ = 0
        DV = 0.0D0
        IF (LCT .AND. IBODY == IBCT) THEN
          CALL SHPRD(X, Y, Z, U, V, W, 1)
          CALL CTLOCATE
          IF (IRETRN == 0) THEN
            JX = IXC; JY = IYC; JZ = IZC
            IM0 = MAT
            DV = DC(IAC)
          ELSE
            IRETRN = 0
            IM0 = -1
          END IF
        END IF
        WRITE(19) IB0, IM0, JX, JY, JZ, DV
      END DO
      CLOSE(19)
      DEALLOCATE(P)
    CASE ('QUIT')
      EXIT
    CASE DEFAULT
      WRITE(0,'(A,A)') 'ARGUS_PROBE: orden desconocida: ', TRIM(CMD)
    END SELECT
  END DO
900 CONTINUE

CONTAINS

  SUBROUTINE DUMP_TABLES(FNAME)
    CHARACTER(LEN=*), INTENT(IN) :: FNAME
    INTEGER*4 :: KS, KB, J
    OPEN(20, FILE=FNAME)
    WRITE(20,'(A,I8)') 'NSURF ', NSURF
    DO KS = 1, NSURF
      WRITE(20,'(I8,I3,1P,10E25.16)') KS, KPLANE(KS), AXX(KS), AXY(KS), AXZ(KS), &
        AYY(KS), AYZ(KS), AZZ(KS), AX(KS), AY(KS), AZ(KS), A0(KS)
    END DO
    WRITE(20,'(A,I8)') 'NBODYS ', NBODYS
    DO KB = 1, NBODYS
      WRITE(20,'(A,I8,A,I8,A,I8,A,I3,A,A4,A)') 'BODY ', KB, ' MAT ', MATER(KB), &
        ' MOTH ', KMOTH(KB), ' MOD ', KBOMO(KB), ' ALIAS [', BALIAS(KB), ']'
      WRITE(20,'(A,I6,1000I7)') ' DGHT ', KDGHT(KB,NXG), (KDGHT(KB,J), J=1,KDGHT(KB,NXG))
      WRITE(20,'(A,I6,1000I7)') ' SURF ', KSURF(KB,NXG), (KSURF(KB,J), J=1,KSURF(KB,NXG))
      WRITE(20,'(A,I6,1000I7)') ' FLAG ', KSURF(KB,NXG), (KFLAG(KB,J), J=1,KSURF(KB,NXG))
      WRITE(20,'(A,I6,1000I7)') ' LIMB ', KBODY(KB,NXG), (KBODY(KB,J), J=1,KBODY(KB,NXG))
    END DO
    CLOSE(20)
  END SUBROUTINE DUMP_TABLES

END PROGRAM ARGUS_PROBE
