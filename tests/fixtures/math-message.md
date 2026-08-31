Definition
$S$ is the Jacobian of the force-feedback error vector with respect to the HCM wrist joint angles.

Let $q\in\mathbb{R}^{7}$ be the HCM joint angles, with the wrist being $j\in\{3,4,5,6\}$ (segments 4–7, axes $\hat z,\hat y,\hat z,\hat x$). Let

$$R_r \in SO(3) \quad\text{(robot-tool, base frame — \texttt{qRobot})}, \qquad R_h(q) \in SO(3)\quad\text{(HCM tool, base frame — \texttt{qHost})}$$

The relative rotation, expressed in the robot-tool frame:

$$R_{\mathrm{rel}}(q) \;=\; R_r^{\top} R_h(q)$$

Swing–twist about the tool roll axis $\hat e_x$, i.e. $R_{\mathrm{rel}} = R_{\mathrm{sw}}R_{\mathrm{tw}}$, gives the error vector the limits act on:

$$a(q) \;=\; \begin{bmatrix} a_0\\ a_1\\ a_2\end{bmatrix} \;=\; \begin{bmatrix} \theta_{\mathrm{tw}}(R_{\mathrm{rel}}) \\ \big[\log R_{\mathrm{sw}}\big]y \\ \big[\log R{\mathrm{sw}}\big]_z \end{bmatrix} \;=\; \begin{bmatrix}\text{roll}\\ \text{pitch}\\ \text{yaw}\end{bmatrix}$$

Then

$$\boxed{\;S \;=\; \frac{\partial a}{\partial q}\;\in\;\mathbb{R}^{3\times 4}, \qquad S_{ij} \;=\; \frac{\partial a_i}{\partial q_j}\;}$$

$S_{ij}$ answers: if wrist motor $j$ moves one radian, how many radians does error component $i$ move, right now, in this configuration?

Row 0 is identically zero, since $a_0$ is overwritten with the robot's joint 7 and has no dependence on $q$. Only rows 1 and 2 are used.

Analytic form (first order)
With $J_\omega \in \mathbb{R}^{3\times4}$ the base-frame angular Jacobian (angularJacobianWrist_), defined by $\omega_h = J_\omega\dot q$:

$$\omega_{\mathrm{rel}} \;=\; R_r^{\top}\omega_h \;=\; R_r^{\top}J_\omega\,\dot q \qquad\text{(holding } R_r \text{ fixed)}$$

The full chain is

$$S \;=\; \underbrace{\frac{\partial a}{\partial \log R_{\mathrm{rel}}}}_{\text{swing–twist}}\; \underbrace{J_r^{-1}\!\left(\log R_{\mathrm{rel}}\right)}_{\text{log-map}}\; \underbrace{R_r^{\top}J_\omega}_{\text{frame}\,+\,\text{kinematics}}$$

where $J_r^{-1}$ is the inverse right Jacobian of $SO(3)$,

$$J_r^{-1}(a) \;=\; I \;+\; \tfrac{1}{2}[a]\times \;+\; \left(\frac{1}{\lVert a\rVert^{2}} - \frac{1+\cos\lVert a\rVert}{2\lVert a\rVert\sin\lVert a\rVert}\right)[a]\times^{2}$$

Since $J_r^{-1}(a) = I + \tfrac12[a]_\times + O(\lVert a\rVert^2)$ and the swing–twist factor $\to I$ as pitch/yaw $\to 0$:

$$S \;\approx\; R_r^{\top} J_\omega$$
